"""The engine: one page view in, a decision and at most three records out.

This is the only module in the package that writes. Everything it writes goes through
:class:`~dsr.store.RecordStore`, and every writing call takes ``source`` as a required
keyword so the audit row names the route that served the write. A hardcoded string
inside a domain method is a defect, and that class of bug has shipped in this codebase
before: a feature's audit log kept naming a path the app had stopped serving.

The chain is the researched data flow, in order and without additions::

    One page view from the seller's snippet
      -> targeting rules and the repeat threshold   (dsr.page_outreach.rules)
      -> the frequency mode and the session damper  (dsr.page_outreach.rules)
      -> a delivery record naming what would be shown  (this module)
      -> a content_stat receipt per buyer interaction  (this module)

Every step reads the record the step before it wrote, so the whole chain is auditable
in order. A view that does not qualify still writes a view record, because the repeat
count is made of views that did not qualify. A page view that is written and then
refused would leave the arithmetic unauditable, which is the one thing this workflow
cannot afford.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dsr.page_outreach import rules as rules_module
from dsr.page_outreach.errors import (
    DuplicateWorkflow,
    InvalidInteraction,
    InvalidWorkflow,
    UnknownDelivery,
    UnknownPath,
    UnknownWorkflow,
)
from dsr.page_outreach.inferences import inferences
from dsr.page_outreach.vocabulary import (
    APP_KINDS,
    BLOCK_KINDS,
    CHANNELS,
    COLLECTIONS,
    DELIVERY_LIMITS,
    DELIVERY_STATES,
    DWELL_SECONDS,
    FREQUENCY_MODES,
    INTERACTIONS,
    REPEAT_VISITS,
    REPEAT_WINDOW_DAYS,
    TARGET_RULE_KINDS,
    THRESHOLDS,
    TRIGGER_PANES,
    UNSOURCED_LIMITS,
    URL_MATCH_MODES,
    WORKFLOW_STATES,
)
from dsr.store import RecordStore

FREQUENCY_MODES_BY_NAME = {str(entry["mode"]): entry for entry in FREQUENCY_MODES}
INTERACTIONS_BY_KIND = {str(entry["kind"]): entry for entry in INTERACTIONS}
INTERACTION_KINDS = tuple(INTERACTIONS_BY_KIND)

#: The largest repeat window a workflow may declare. Ninety days is a quarter, and a
#: window longer than a quarter is not a repeat damper.
MAX_WINDOW_DAYS = 90


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment else ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _data(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("data") or {}


class PageOutreach:
    """Browsing-triggered outreach, over one record store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def _scoped(
        self,
        collection: str,
        *,
        room_id: str | None = None,
        where: dict[str, Any] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Read a collection by room, then filter on the JSON fields the caller named.

        Room scoping goes through ``store.list`` because ``room_id`` is an envelope
        column, not a field inside ``data``. ``store.find`` reads ``data`` through the
        dynamic index and cannot see the envelope, so a ``find`` on ``room_id`` matches
        nothing at all and the room filter looks like it worked. That is why this
        helper exists rather than a direct ``find`` at each call site.
        """
        rows = self.store.list(collection, room_id=room_id, limit=1000)
        if not where:
            return rows[:limit]
        return [
            row
            for row in rows
            if all(str(_data(row).get(key, "")) == str(value) for key, value in where.items())
        ][:limit]

    # ----------------------------------------------------------------- #
    # Published vocabulary and the decision record
    # ----------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every name, number and threshold this workflow uses, served as data."""
        return {
            "ticket": "WF-106",
            "collections": dict(COLLECTIONS),
            "frequency_modes": [dict(entry) for entry in FREQUENCY_MODES],
            "default_frequency": next(
                str(entry["mode"]) for entry in FREQUENCY_MODES if entry["default"]
            ),
            "interactions": [dict(entry) for entry in INTERACTIONS],
            "interaction_kinds": list(INTERACTION_KINDS),
            "engagement_interactions": [
                str(entry["kind"]) for entry in INTERACTIONS if entry["engagement"]
            ],
            "session_hiding_interactions": [
                str(entry["kind"])
                for entry in INTERACTIONS
                if entry["kind"] in ("dismissed", "messenger_opened")
            ],
            "trigger_signals": ["visited_url", "time_on_page"],
            "trigger_panes": [dict(entry) for entry in TRIGGER_PANES],
            "rule_kinds": list(TARGET_RULE_KINDS),
            "url_match_modes": list(URL_MATCH_MODES),
            "rule_table": rules_module.rule_table(),
            "channels": list(CHANNELS),
            "workflow_states": list(WORKFLOW_STATES),
            "block_kinds": list(BLOCK_KINDS),
            "app_kinds": list(APP_KINDS),
            "delivery_states": list(DELIVERY_STATES),
            "thresholds": [dict(entry) for entry in THRESHOLDS],
            "sourced_thresholds": [entry["kind"] for entry in THRESHOLDS if entry["sourced"]],
            "derived_thresholds": [entry["kind"] for entry in THRESHOLDS if not entry["sourced"]],
            "page_view_fields": sorted(rules_module.ALLOWED_FIELDS),
            "display_only_fields": list(rules_module.DISPLAY_ONLY_FIELDS),
            "unsourced_limits": [dict(entry) for entry in UNSOURCED_LIMITS],
            **DELIVERY_LIMITS,
        }

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return inferences()

    # ----------------------------------------------------------------- #
    # Workflows
    # ----------------------------------------------------------------- #

    def create_workflow(
        self,
        payload: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Save a workflow: the trigger, the panes, the blocks and the paths.

        A draft unless the caller says ``state: live``, because the researched flow
        ends with the seller setting it live and a workflow that published itself the
        moment it was created would publish the seller's half-finished rules to the
        whole audience.
        """
        data = self._read_workflow_payload(payload)
        if self._named(room_id, data["name"]):
            raise DuplicateWorkflow(
                f"a workflow named {data['name']!r} already exists in this room; "
                "two workflows with one name would show the same block twice"
            )
        record = self.store.create(
            COLLECTIONS["workflows"],
            data,
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.read_workflow(record["id"])

    def _named(self, room_id: str | None, name: str) -> list[dict[str, Any]]:
        """The live workflows in a room that carry this name."""
        return [
            row
            for row in self._scoped(COLLECTIONS["workflows"], room_id=room_id)
            if _text(_data(row).get("name")) == name
        ]

    def update_workflow(
        self,
        workflow_id: str,
        payload: Any,
        *,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Replace a workflow's rules and panes with what the caller sent.

        A full replacement rather than a merge. A merge of a rule list would leave a
        rule the seller deleted in place forever, and the page has no way to express
        "remove this one rule" against a patch.
        """
        current = self._require_workflow(workflow_id)
        data = self._read_workflow_payload(payload)
        renamed = data["name"] != _text(_data(current).get("name"))
        if renamed and self._named(current.get("room_id"), data["name"]):
            raise DuplicateWorkflow(
                f"a workflow named {data['name']!r} already exists in this room"
            )
        return self.store.update(workflow_id, data, actor=actor, source=source)

    def set_state(
        self,
        workflow_id: str,
        state: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Move a workflow between draft and live.

        A separate route because the researched flow's last step is this one and it is
        the only write that changes what buyers see.
        """
        wanted = _text(state)
        if wanted not in WORKFLOW_STATES:
            raise InvalidWorkflow(
                f"state must be one of {', '.join(WORKFLOW_STATES)}, got {state!r}"
            )
        self._require_workflow(workflow_id)
        return self.store.update(workflow_id, {"state": wanted}, actor=actor, source=source)

    def delete_workflow(
        self,
        workflow_id: str,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Retire a workflow.

        A soft delete, so the audit trail keeps the rules that were in force for every
        view recorded while it was live. A hard delete would leave those views pointing
        at a definition nobody can read.
        """
        self._require_workflow(workflow_id)
        return self.store.delete(workflow_id, actor=actor, source=source)

    def workflows(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        """The workflows in a room, drafts included."""
        return [
            self._workflow_view(row)
            for row in self._scoped(COLLECTIONS["workflows"], room_id=room_id)
        ]

    def read_workflow(self, workflow_id: str) -> dict[str, Any]:
        """One workflow, with its rules, panes, blocks and paths."""
        return self._workflow_view(self._require_workflow(workflow_id))

    def _require_workflow(self, workflow_id: str) -> dict[str, Any]:
        record = self.store.get(_text(workflow_id))
        if record is None or record.get("collection") != COLLECTIONS["workflows"]:
            raise UnknownWorkflow(f"no workflow with id {workflow_id!r}")
        return record

    def _read_workflow_payload(self, payload: Any) -> dict[str, Any]:
        """Validate a workflow definition into the shape the record holds."""
        if not isinstance(payload, dict):
            raise InvalidWorkflow("a workflow must be an object")

        name = _text(payload.get("name"))
        if not name:
            raise InvalidWorkflow("name is required")

        frequency = _text(payload.get("frequency")) or self.vocabulary()["default_frequency"]
        if frequency not in FREQUENCY_MODES_BY_NAME:
            raise InvalidWorkflow(
                f"frequency must be one of {', '.join(sorted(FREQUENCY_MODES_BY_NAME))}, "
                f"got {frequency!r}"
            )

        channel = _text(payload.get("channel")) or CHANNELS[0]
        if channel not in CHANNELS:
            raise InvalidWorkflow(
                f"this build can only honour {', '.join(CHANNELS)}. The research names "
                f"POST /messages with message_type in_app as the API equivalent of the in-app "
                f"block, and this product has no outbound transport, so {channel!r} would be a "
                "stored promise nothing could keep"
            )

        state = _text(payload.get("state")) or "draft"
        if state not in WORKFLOW_STATES:
            raise InvalidWorkflow(
                f"state must be one of {', '.join(WORKFLOW_STATES)}, got {state!r}"
            )

        visits = self._read_int(
            payload.get("repeat_visits", REPEAT_VISITS), "repeat_visits", 1, 365
        )
        window_days = self._read_int(
            payload.get("repeat_window_days", REPEAT_WINDOW_DAYS),
            "repeat_window_days",
            1,
            MAX_WINDOW_DAYS,
        )
        dwell = self._read_int(
            payload.get("dwell_seconds", DWELL_SECONDS), "dwell_seconds", 0, 86400
        )

        return {
            "name": name,
            "frequency": frequency,
            "channel": channel,
            "state": state,
            "repeat_visits": visits,
            "repeat_window_days": window_days,
            "dwell_seconds": dwell,
            "rules": self._read_rules(payload.get("rules")),
            "audience": self._read_audience(payload.get("audience")),
            "scheduling": self._read_scheduling(payload.get("scheduling"), state),
            "goal_name": _text(payload.get("goal_name")),
            "blocks": self._read_blocks(payload.get("blocks")),
            "paths": self._read_paths(payload.get("paths")),
            "note": _text(payload.get("note")),
        }

    @staticmethod
    def _read_int(value: Any, field_name: str, low: int, high: int) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidWorkflow(f"{field_name} must be a whole number, not {value!r}")
        if isinstance(value, float) and not value.is_integer():
            raise InvalidWorkflow(f"{field_name} must be a whole number, not {value!r}")
        number = int(value)
        if number < low or number > high:
            raise InvalidWorkflow(f"{field_name} must be between {low} and {high}, got {number}")
        return number

    def _read_rules(self, raw: Any) -> list[dict[str, Any]]:
        """Read the targeting rules.

        An unknown kind is refused rather than stored. A rule on a kind no matcher
        implements is a rule that silently never fires, and a seller who has written a
        rule expects it to do something.
        """
        if raw is None:
            return []
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise InvalidWorkflow("rules must be a list of targeting rules")
        rules: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise InvalidWorkflow("a targeting rule must be an object")
            kind = _text(entry.get("kind"))
            if kind not in TARGET_RULE_KINDS:
                raise InvalidWorkflow(
                    f"a targeting rule has kind {kind or '(blank)'!r}; this workflow "
                    f"implements {', '.join(TARGET_RULE_KINDS)}"
                )
            value = _text(entry.get("value"))
            if not value:
                raise InvalidWorkflow(f"a {kind} rule needs a value")
            mode = _text(entry.get("mode")) or ("prefix" if kind == "url" else "equals")
            if kind == "url" and mode not in URL_MATCH_MODES:
                raise InvalidWorkflow(
                    f"a url rule has mode {mode!r}; this workflow implements "
                    f"{', '.join(URL_MATCH_MODES)}"
                )
            if kind == "dwell":
                self._read_rule_dwell(value)
            key = (kind, mode, value)
            if key in seen:
                raise InvalidWorkflow(
                    f"the {kind} rule on {value!r} is listed twice; a duplicated rule can "
                    "never change the outcome and hides a rule the seller meant to add"
                )
            seen.add(key)
            rules.append(
                {
                    "kind": kind,
                    "mode": mode,
                    "value": value,
                    "sourced": bool(entry.get("sourced")),
                    "note": _text(entry.get("note")),
                }
            )
        return rules

    @staticmethod
    def _read_rule_dwell(value: str) -> int:
        """Read the number out of a dwell rule's value and check its range.

        A rule value is text because it lives in the same list as a path, so a dwell
        rule's ``60`` arrives as the string ``"60"``. It is read rather than typed, and
        stored as written, so the record a seller reads shows the value they typed.
        """
        try:
            number = int(value.strip())
        except ValueError as exc:
            raise InvalidWorkflow(
                f"a dwell rule's value must be a whole number of seconds, got {value!r}"
            ) from exc
        if number < 1 or number > 86400:
            raise InvalidWorkflow(f"a dwell rule's value must be between 1 and 86400, got {number}")
        return number

    @staticmethod
    def _read_audience(raw: Any) -> dict[str, list[str]]:
        """Read the audience pane as three lists of non-blank strings."""
        if raw is None:
            return {"company_keys": [], "tags": [], "segments": []}
        if not isinstance(raw, dict):
            raise InvalidWorkflow("audience must be an object with company_keys, tags and segments")
        unknown = sorted(set(raw) - {"company_keys", "tags", "segments"})
        if unknown:
            raise InvalidWorkflow(
                f"audience carries key(s) {', '.join(unknown)}; the pane holds company_keys, "
                "tags and segments"
            )
        audience: dict[str, list[str]] = {}
        for name in ("company_keys", "tags", "segments"):
            entries = raw.get(name) or []
            if isinstance(entries, str) or not isinstance(entries, (list, tuple)):
                raise InvalidWorkflow(f"audience.{name} must be a list")
            values: list[str] = []
            for entry in entries:
                text = _text(entry)
                if not text:
                    raise InvalidWorkflow(f"audience.{name} must not contain a blank entry")
                if text not in values:
                    values.append(text)
            audience[name] = values
        return audience

    @staticmethod
    def _read_scheduling(raw: Any, state: str) -> dict[str, Any]:
        """Read the scheduling pane.

        ``starts_at`` and ``ends_at`` are stored as written rather than parsed here.
        They are display values for the seller, and parsing a moment the workflow never
        compares against would be a validation with no reader.
        """
        if raw is None:
            return {"state": state, "starts_at": "", "ends_at": ""}
        if not isinstance(raw, dict):
            raise InvalidWorkflow("scheduling must be an object")
        unknown = sorted(set(raw) - {"state", "starts_at", "ends_at"})
        if unknown:
            raise InvalidWorkflow(
                f"scheduling carries key(s) {', '.join(unknown)}; the pane holds state, "
                "starts_at and ends_at"
            )
        wanted = _text(raw.get("state")) or state
        if wanted not in WORKFLOW_STATES:
            raise InvalidWorkflow(
                f"scheduling.state must be one of {', '.join(WORKFLOW_STATES)}, got {wanted!r}"
            )
        return {
            "state": wanted,
            "starts_at": _text(raw.get("starts_at")),
            "ends_at": _text(raw.get("ends_at")),
        }

    @staticmethod
    def _read_blocks(raw: Any) -> list[dict[str, Any]]:
        """Read the welcome message and its apps.

        A block with no text is refused. A welcome message with nothing in it is the
        one artefact in this workflow that goes straight to a buyer, and an empty one
        shows a buyer a box with no words in it.
        """
        if raw is None:
            return []
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise InvalidWorkflow("blocks must be a list")
        blocks: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise InvalidWorkflow("a block must be an object")
            kind = _text(entry.get("kind")) or "message"
            if kind not in BLOCK_KINDS:
                raise InvalidWorkflow(
                    f"a block has kind {kind!r}; the researched flow names {', '.join(BLOCK_KINDS)}"
                )
            text = _text(entry.get("text"))
            if not text:
                raise InvalidWorkflow(
                    f"a {kind} block needs text; a block with no words in it reaches a buyer"
                )
            apps = entry.get("apps") or []
            if isinstance(apps, str) or not isinstance(apps, (list, tuple)):
                raise InvalidWorkflow("a block's apps must be a list")
            read_apps: list[dict[str, Any]] = []
            for app in apps:
                if not isinstance(app, dict):
                    raise InvalidWorkflow("an app must be an object")
                app_kind = _text(app.get("kind")) or "article"
                if app_kind not in APP_KINDS:
                    raise InvalidWorkflow(
                        f"an app has kind {app_kind!r}; the flow names {', '.join(APP_KINDS)}"
                    )
                if not _text(app.get("title")):
                    raise InvalidWorkflow("an app needs a title")
                read_apps.append(
                    {
                        "kind": app_kind,
                        "title": _text(app.get("title")),
                        "url": _text(app.get("url")),
                        "note": _text(app.get("note")),
                    }
                )
            blocks.append(
                {
                    "kind": kind,
                    "text": text,
                    "apps": read_apps,
                    "closes": bool(entry.get("closes")),
                }
            )
        return blocks

    def _read_paths(self, raw: Any) -> list[dict[str, Any]]:
        """Read the branch paths.

        Each path carries a ``key`` the buyer's answer resolves to, and the first block
        whose key matches becomes the next thing shown. Two paths on one key is refused,
        because the buyer answered one question and two branches cannot both be right.
        """
        if raw is None:
            return []
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise InvalidWorkflow("paths must be a list")
        paths: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise InvalidWorkflow("a path must be an object")
            key = _text(entry.get("key"))
            label = _text(entry.get("label"))
            if not key:
                raise InvalidWorkflow("a path needs a key; the buyer's answer resolves to it")
            if not label:
                raise InvalidWorkflow(f"path {key!r} needs a label; it is the text on the button")
            if key in seen:
                raise InvalidWorkflow(
                    f"two paths answer to {key!r}; the buyer answered one question and two "
                    "branches cannot both be right"
                )
            seen.add(key)
            paths.append(
                {
                    "key": key,
                    "label": label,
                    "next": _text(entry.get("next")),
                    "closes": bool(entry.get("closes", False)),
                    "note": _text(entry.get("note")),
                }
            )
        return paths

    def _workflow_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            **data,
            "id": record["id"],
            "room_id": record.get("room_id") or "",
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "rule_count": len(data.get("rules") or []),
            "path_count": len(data.get("paths") or []),
            "block_count": len(data.get("blocks") or []),
        }

    # ----------------------------------------------------------------- #
    # Page views
    # ----------------------------------------------------------------- #

    def evaluate(self, payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
        """Say what the rules decide, and write nothing.

        The report and the commit are two routes on purpose. This one never writes a
        view, so a caller watching a prospect before deciding anything gets every
        decision without filling the store with visits that were only ever going to be
        counted.
        """
        view = rules_module.parse_page_view(payload, now=now)
        workflow = self._require_workflow(view.workflow_id)
        data = _data(workflow)
        state = self._state_of(data)
        if state != "live":
            return {
                "workflow_id": view.workflow_id,
                "workflow_name": _text(data.get("name")),
                "state": state,
                "show": False,
                "stopped_by": "not_live",
                "reason": f"the workflow is {state}; only a live workflow shows a block",
                "wrote": False,
                "matching_visits": 0,
                "visits_required": int(data.get("repeat_visits") or REPEAT_VISITS),
                "window_days": int(data.get("repeat_window_days") or REPEAT_WINDOW_DAYS),
                "session_id": view.session_id,
                "rule_matches": [],
            }
        return {
            **self._decide(view, workflow, now=now, record_view=False),
            "workflow_name": _text(data.get("name")),
            "state": state,
            "wrote": False,
        }

    def record_view(
        self,
        payload: Any,
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record a page view and commit the decision.

        Two or three records are written, never one and never five:

        * the view, always, because the repeat count is made of the visits that did
          not qualify as well as the ones that did;
        * the delivery, when the block is shown;
        * the receipt, never from here. A receipt is a buyer action and this route has
          no buyer in it.
        """
        reference = _now(now)
        view = rules_module.parse_page_view(payload, now=reference)
        workflow = self._require_workflow(view.workflow_id)
        room = room_id or workflow.get("room_id") or _text(_data(workflow).get("room_id"))

        audience_reason = self._audience_reason(_data(workflow), view)
        if audience_reason:
            self._write_view(
                view, workflow, room, matched=False, actor=actor, source=source, now=reference
            )
            return {
                "workflow_id": view.workflow_id,
                "state": self._state_of(_data(workflow)),
                "show": False,
                "stopped_by": "audience",
                "reason": audience_reason,
                "wrote": True,
                "matching_visits": 0,
                "visits_required": int(_data(workflow).get("repeat_visits") or REPEAT_VISITS),
                "window_days": int(_data(workflow).get("repeat_window_days") or REPEAT_WINDOW_DAYS),
                "session_id": view.session_id,
                "rule_matches": [],
                "delivery": None,
                "receipt": None,
            }

        decision = self._decide(
            view,
            workflow,
            now=reference,
            record_view=True,
            actor=actor,
            source=source,
            room_id=room,
        )
        result = {
            **decision,
            "state": self._state_of(_data(workflow)),
            "wrote": True,
            "delivery": None,
            "receipt": None,
        }
        if decision["show"]:
            delivery = self._show(
                workflow, view, decision, room_id=room, actor=actor, source=source, now=reference
            )
            result["delivery"] = delivery
        return result

    def _decide(
        self,
        view: rules_module.PageView,
        workflow: dict[str, Any],
        *,
        now: datetime,
        record_view: bool,
        actor: str | None = None,
        source: str = "seed",
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """The four gates, and the view record when the caller asked for one."""
        reference = _now(now)
        data = _data(workflow)
        room = room_id or workflow.get("room_id") or _text(data.get("room_id"))
        window_days = int(data.get("repeat_window_days") or REPEAT_WINDOW_DAYS)
        required = int(data.get("repeat_visits") or REPEAT_VISITS)

        matched_now = rules_module.rules_matched(
            rules_module.match_rules(
                list(data.get("rules") or []),
                view,
                dwell_seconds=int(data.get("dwell_seconds") or DWELL_SECONDS),
            )
        )
        # The window arithmetic lives in the pure module, so it can be tested on its
        # own. This view is added to the count because a seller reading the decision
        # needs to see the visit that triggered it in the total.
        matching_visits = rules_module.count_matching_visits(
            [_data(row) for row in self._recorded_views(view)],
            now=reference,
            window_days=window_days,
        ) + (1 if matched_now else 0)

        shown, interacted, engaged = self._buyer_counts(view)
        session_interactions = self._session_interactions(view)

        decision = rules_module.decide(
            view,
            rules=list(data.get("rules") or []),
            frequency=_text(data.get("frequency")) or "seen",
            shown=shown,
            interacted=interacted,
            engaged=engaged,
            session_interactions=session_interactions,
            dwell_seconds=int(data.get("dwell_seconds") or DWELL_SECONDS),
            matching_visits=matching_visits,
            visits_required=required,
            window_days=window_days,
        )

        if record_view:
            self._write_view(
                view,
                workflow,
                room,
                matched=matched_now,
                actor=actor,
                source=source,
                now=reference,
                counts={
                    "matching_visits": matching_visits,
                    "visits_required": required,
                    "shown": shown,
                    "interacted": interacted,
                    "engaged": engaged,
                    "session_interactions": session_interactions,
                },
                stopped_by=decision.stopped_by,
            )
        return {
            "workflow_id": view.workflow_id,
            "show": decision.show,
            "stopped_by": decision.stopped_by,
            "reason": decision.reason,
            "matching_visits": decision.matching_visits,
            "visits_required": decision.visits_required,
            "window_days": decision.window_days,
            "session_id": decision.session_id,
            "rule_matches": [entry.as_dict() for entry in decision.rule_matches],
        }

    def _write_view(
        self,
        view: rules_module.PageView,
        workflow: dict[str, Any],
        room_id: str | None,
        *,
        matched: bool,
        actor: str | None,
        source: str,
        now: datetime,
        counts: dict[str, Any] | None = None,
        stopped_by: str = "",
    ) -> dict[str, Any]:
        """One page view, with the counts that were in force when it was seen."""
        data = _data(workflow)
        return self.store.create(
            COLLECTIONS["views"],
            {
                "workflow_id": view.workflow_id,
                "workflow_name": _text(data.get("name")),
                "visitor_key": view.visitor_key,
                "company_key": view.company_key,
                "session_id": view.session_id,
                "matched": bool(matched),
                "stopped_by": stopped_by,
                "counts": counts or {},
                **view.to_dict(),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _recorded_views(self, view: rules_module.PageView) -> list[dict[str, Any]]:
        """Every recorded view for this workflow and visitor, newest first.

        Unfiltered by window on purpose. The window arithmetic is
        :func:`~dsr.page_outreach.rules.count_matching_visits`'s job and it lives in the
        pure module, so the engine hands it the whole recorded history rather than
        applying a second copy of the same date comparison.
        """
        rows = self._scoped(
            COLLECTIONS["views"],
            where={
                "workflow_id": view.workflow_id,
                "visitor_key": view.visitor_key,
            },
            limit=200,
        )
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows

    def _buyer_counts(self, view: rules_module.PageView) -> tuple[int, int, int]:
        """How many shows, interactions and engagements this buyer already has.

        Counted over deliveries and receipts for this workflow and this visitor. The
        ``seen`` mode reads the first number, ``any_interaction`` the second, and
        ``engaged_with`` the third, which is why all three are computed here rather
        than in the mode that needs them.
        """
        where = {"workflow_id": view.workflow_id, "visitor_key": view.visitor_key}
        deliveries = self._scoped(COLLECTIONS["deliveries"], where=where, limit=200)
        receipts = self._scoped(COLLECTIONS["receipts"], where=where, limit=500)
        shown = len(deliveries)
        interacted = len(
            [row for row in receipts if _text(_data(row).get("kind")) in INTERACTIONS_BY_KIND]
        )
        engaged = len(
            [row for row in receipts if rules_module.is_engagement(_text(_data(row).get("kind")))]
        )
        return (shown, interacted, engaged)

    def _session_interactions(self, view: rules_module.PageView) -> list[str]:
        """The interaction kinds recorded against this session for this workflow.

        Read from the receipts rather than from a session table, so the damper has no
        state of its own to fall out of step with the receipts it is derived from.
        """
        rows = self._scoped(
            COLLECTIONS["receipts"],
            where={
                "workflow_id": view.workflow_id,
                "visitor_key": view.visitor_key,
                "session_id": view.session_id,
            },
            limit=200,
        )
        kinds: list[str] = []
        for row in rows:
            kind = _text(_data(row).get("kind"))
            if kind and kind not in kinds:
                kinds.append(kind)
        return kinds

    @staticmethod
    def _audience_reason(data: dict[str, Any], view: rules_module.PageView) -> str:
        """Whether the audience pane excludes this page view, and why.

        A view with no company key is not excluded. The snippet identifies some
        visitors and not others, and refusing the unidentified ones would throw away
        exactly the anonymous traffic this workflow exists to catch.
        """
        audience = data.get("audience") or {}
        company_keys = [str(entry).lower() for entry in audience.get("company_keys") or []]
        if company_keys:
            if view.company_key and view.company_key.lower() in company_keys:
                return ""
            if not view.company_key:
                return (
                    "the audience names companies and this view carries no company key; "
                    "the view is counted but the block is not shown, because an "
                    "unidentified visitor cannot be checked against a list"
                )
            return f"{view.company_key} is not on the audience company list"
        segments = [str(entry).lower() for entry in audience.get("segments") or []]
        if segments:
            return (
                "the audience names segments and this build has no segment attribute on a "
                "page view; the segments pane is served as data and is not enforced"
            )
        return ""

    @staticmethod
    def _state_of(data: dict[str, Any]) -> str:
        scheduling = data.get("scheduling") or {}
        return _text(scheduling.get("state")) or _text(data.get("state")) or "draft"

    # ----------------------------------------------------------------- #
    # Deliveries: what would be shown
    # ----------------------------------------------------------------- #

    def _show(
        self,
        workflow: dict[str, Any],
        view: rules_module.PageView,
        decision: dict[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
        now: datetime,
    ) -> dict[str, Any]:
        """Write the delivery record: what would be shown, to whom, in this session."""
        data = _data(workflow)
        paths = list(data.get("paths") or [])
        return self._delivery_view(
            self.store.create(
                COLLECTIONS["deliveries"],
                {
                    "workflow_id": workflow["id"],
                    "workflow_name": _text(data.get("name")),
                    "visitor_key": view.visitor_key,
                    "company_key": view.company_key,
                    "session_id": view.session_id,
                    "channel": _text(data.get("channel")) or CHANNELS[0],
                    "message_type": "in_app",
                    "state": "shown",
                    "frequency": _text(data.get("frequency")) or "seen",
                    "blocks": list(data.get("blocks") or []),
                    "first_path_key": paths[0]["key"] if paths else "",
                    "goal_name": _text(data.get("goal_name")),
                    "reason": decision["reason"],
                    "trigger": {
                        "matching_visits": decision["matching_visits"],
                        "visits_required": decision["visits_required"],
                        "window_days": decision["window_days"],
                        "path": view.path,
                        "dwell_seconds": view.dwell_seconds,
                        "rule_matches": decision["rule_matches"],
                    },
                    "shown_at": _iso(now),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
        )

    def deliveries(
        self, *, room_id: str | None = None, visitor_key: str = "", workflow_id: str = ""
    ) -> list[dict[str, Any]]:
        """The deliveries in a room, newest first."""
        where: dict[str, Any] = {}
        if visitor_key:
            where["visitor_key"] = visitor_key
        if workflow_id:
            where["workflow_id"] = workflow_id
        rows = self._scoped(COLLECTIONS["deliveries"], room_id=room_id, where=where, limit=500)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return [self._delivery_view(row) for row in rows]

    def read_delivery(self, delivery_id: str) -> dict[str, Any]:
        """One delivery, with the receipts recorded against it."""
        record = self.store.get(_text(delivery_id))
        if record is None or record.get("collection") != COLLECTIONS["deliveries"]:
            raise UnknownDelivery(f"no delivery with id {delivery_id!r}")
        return self._delivery_view(record)

    def _delivery_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        receipts = self._scoped(
            COLLECTIONS["receipts"],
            room_id=record.get("room_id"),
            where={"delivery_id": record["id"]},
            limit=200,
        )
        kinds = [_text(_data(row).get("kind")) for row in receipts]
        return {
            **data,
            "id": record["id"],
            "room_id": record.get("room_id") or "",
            "created_at": record.get("created_at"),
            "state": self._delivery_state(_text(data.get("state")), kinds),
            "receipt_count": len(receipts),
            "receipt_kinds": sorted(set(kinds)),
            "session_hidden": rules_module.is_session_hiding(
                next((kind for kind in kinds if rules_module.is_session_hiding(kind)), "")
            ),
        }

    @staticmethod
    def _delivery_state(base: str, kinds: list[str]) -> str:
        """The delivery's state, derived from its receipts.

        Derived rather than set, so a delivery can never hold a state its own receipts
        do not support. A goal or a path selection reads as engaged because the
        research treats the path selection as the engagement; a dismissal or a
        Messenger open reads as hidden for the session and no further.
        """
        if any(rules_module.is_engagement(kind) for kind in kinds):
            return "engaged"
        if any(rules_module.is_session_hiding(kind) for kind in kinds):
            return "hidden_for_session"
        if kinds:
            return "interacted"
        return base or "shown"

    # ----------------------------------------------------------------- #
    # Receipts: the content_stat events
    # ----------------------------------------------------------------- #

    def record_interaction(
        self,
        delivery_id: str,
        payload: Any,
        *,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record one buyer interaction as a content_stat receipt.

        A dismissal or a Messenger open hides the block for the rest of the session,
        and a path selection or a goal counts as engaging. Both are read from the
        receipt afterwards rather than written as a flag, so the damper has no state
        that can disagree with the events it is derived from.

        A ``path_selected`` receipt has to name a key the workflow declared. The
        researched flow branches on the buyer's answer, so an answer that resolves to
        no branch would leave the receipt pointing at nothing.
        """
        reference = _now(now)
        delivery = self.store.get(_text(delivery_id))
        if delivery is None or delivery.get("collection") != COLLECTIONS["deliveries"]:
            raise UnknownDelivery(f"no delivery with id {delivery_id!r}")
        if not isinstance(payload, dict):
            raise InvalidInteraction("an interaction must be an object")

        unknown = sorted(set(payload) - {"kind", "path_key", "note", "occurred_at"})
        if unknown:
            raise InvalidInteraction(
                f"the interaction carries field(s) {', '.join(unknown)}; this workflow "
                "records kind, path_key and note"
            )
        kind = _text(payload.get("kind"))
        if kind not in INTERACTIONS_BY_KIND:
            raise InvalidInteraction(
                f"kind must be one of {', '.join(INTERACTION_KINDS)}, got {kind or '(blank)'!r}"
            )

        data = _data(delivery)
        workflow = self.store.get(_text(data.get("workflow_id")))
        if workflow is None:
            raise UnknownWorkflow(
                f"delivery {delivery_id!r} names workflow {_text(data.get('workflow_id'))!r}, "
                "which no longer exists; the receipt is refused rather than written "
                "against nothing"
            )
        workflow_data = _data(workflow)

        path_key = _text(payload.get("path_key"))
        if kind == "path_selected":
            paths = list(workflow_data.get("paths") or [])
            keys = [str(entry.get("key")) for entry in paths]
            if not keys:
                raise UnknownPath(
                    "this workflow declares no paths, so a path selection has nothing to "
                    "resolve to. The researched flow branches on the buyer's answer."
                )
            if path_key not in keys:
                raise UnknownPath(
                    f"path_key must be one of {', '.join(keys)}, got {path_key or '(blank)'!r}"
                )

        receipt = self.store.create(
            COLLECTIONS["receipts"],
            {
                "delivery_id": delivery["id"],
                "workflow_id": delivery["id"] and _text(data.get("workflow_id")),
                "workflow_name": _text(workflow_data.get("name")),
                "visitor_key": _text(data.get("visitor_key")),
                "company_key": _text(data.get("company_key")),
                "session_id": _text(data.get("session_id")),
                "kind": kind,
                "content_stat": _text(INTERACTIONS_BY_KIND[kind]["content_stat"]),
                "engagement": bool(INTERACTIONS_BY_KIND[kind]["engagement"]),
                "hides_for_session": rules_module.is_session_hiding(kind),
                "path_key": path_key,
                "path_label": self._path_label(workflow_data, path_key),
                "note": _text(payload.get("note")),
                "occurred_at": _text(payload.get("occurred_at")) or _iso(reference),
            },
            room_id=delivery.get("room_id"),
            actor=actor,
            source=source,
        )
        return {
            "receipt": {**receipt, "data": _data(receipt)},
            "delivery": self.read_delivery(delivery["id"]),
            "workflow": {
                "id": workflow["id"],
                "frequency": _text(workflow_data.get("frequency")),
                "state": self._state_of(workflow_data),
            },
        }

    @staticmethod
    def _path_label(workflow_data: dict[str, Any], path_key: str) -> str:
        for entry in workflow_data.get("paths") or []:
            if str(entry.get("key")) == path_key:
                return str(entry.get("label") or "")
        return ""

    def receipts(
        self,
        *,
        room_id: str | None = None,
        visitor_key: str = "",
        workflow_id: str = "",
        delivery_id: str = "",
    ) -> list[dict[str, Any]]:
        """The receipts recorded, newest first."""
        where: dict[str, Any] = {}
        if visitor_key:
            where["visitor_key"] = visitor_key
        if workflow_id:
            where["workflow_id"] = workflow_id
        if delivery_id:
            where["delivery_id"] = delivery_id
        rows = self._scoped(COLLECTIONS["receipts"], room_id=room_id, where=where, limit=500)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return [
            {**_data(row), "id": row["id"], "created_at": row.get("created_at")} for row in rows
        ]

    # ----------------------------------------------------------------- #
    # Summary and the read side a client needs
    # ----------------------------------------------------------------- #

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """The room at a glance: workflows, views, blocks shown and receipts.

        Every number is a count over this feature's own four collections. Nothing is
        read from another feature, so a room whose dependencies have not seeded reads
        as zeros rather than failing.
        """
        workflows = self.workflows(room_id=room_id)
        deliveries = self.deliveries(room_id=room_id)
        receipts = self.receipts(room_id=room_id)
        views = self._scoped(COLLECTIONS["views"], room_id=room_id, limit=1000)
        by_kind: dict[str, int] = {}
        for row in receipts:
            key = str(row.get("kind") or "unknown")
            by_kind[key] = by_kind.get(key, 0) + 1
        engaged = sum(1 for row in receipts if rules_module.is_engagement(str(row.get("kind"))))
        return {
            "room_id": room_id or "",
            "workflows": len(workflows),
            "live_workflows": len([entry for entry in workflows if entry.get("state") == "live"]),
            "draft_workflows": len([entry for entry in workflows if entry.get("state") == "draft"]),
            "views": len(views),
            "matched_views": len([row for row in views if _data(row).get("matched")]),
            "deliveries": len(deliveries),
            "receipts": len(receipts),
            "receipt_kinds": dict(sorted(by_kind.items())),
            "engaged_receipts": engaged,
            "hidden_for_session": len([row for row in receipts if row.get("hides_for_session")]),
            **DELIVERY_LIMITS,
        }

    def prospects(
        self,
        *,
        room_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Every visitor this room knows about, and how far each one has got.

        Built from the views rather than from the deliveries, because the interesting
        rows are the buyers who browsed a high-intent page and did not yet qualify. A
        view built from the deliveries alone can only show who was shown the block, so
        the half of the screen a seller most needs would be empty.
        """
        reference = _now(now)
        rows = self._scoped(COLLECTIONS["views"], room_id=room_id, limit=1000)
        by_visitor: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = _data(row)
            key = _text(data.get("visitor_key")) or "unknown"
            entry = by_visitor.setdefault(
                key,
                {
                    "visitor_key": key,
                    "company_key": _text(data.get("company_key")),
                    "visits": 0,
                    "matched_visits": 0,
                    "paths": [],
                    "workflows": [],
                    "last_seen_at": "",
                    "deliveries": 0,
                    "engaged": False,
                    "hidden_for_session": False,
                },
            )
            entry["visits"] += 1
            if data.get("matched"):
                entry["matched_visits"] += 1
            path = _text(data.get("path"))
            if path and path not in entry["paths"]:
                entry["paths"].append(path)
            workflow_name = _text(data.get("workflow_name"))
            if workflow_name and workflow_name not in entry["workflows"]:
                entry["workflows"].append(workflow_name)
            moment = _text(data.get("visited_at"))
            if moment > entry["last_seen_at"]:
                entry["last_seen_at"] = moment

        for delivery in self.deliveries(room_id=room_id):
            key = delivery["visitor_key"]
            entry = by_visitor.setdefault(
                key or "unknown",
                {
                    "visitor_key": key,
                    "company_key": delivery.get("company_key") or "",
                    "visits": 0,
                    "matched_visits": 0,
                    "paths": [],
                    "workflows": [],
                    "last_seen_at": "",
                    "deliveries": 0,
                    "engaged": False,
                    "hidden_for_session": False,
                },
            )
            entry["deliveries"] += 1
            entry["engaged"] = entry["engaged"] or delivery["state"] == "engaged"
            entry["hidden_for_session"] = (
                entry["hidden_for_session"] or delivery["state"] == "hidden_for_session"
            )
            if str(delivery.get("shown_at") or "") > entry["last_seen_at"]:
                entry["last_seen_at"] = str(delivery.get("shown_at") or "")

        prospects = sorted(
            by_visitor.values(), key=lambda entry: entry["last_seen_at"], reverse=True
        )
        return {
            "room_id": room_id or "",
            "count": len(prospects),
            "shown_to": len([entry for entry in prospects if entry["deliveries"]]),
            "not_shown_to": len([entry for entry in prospects if not entry["deliveries"]]),
            "engaged": len([entry for entry in prospects if entry["engaged"]]),
            "hidden_for_session": len(
                [entry for entry in prospects if entry["hidden_for_session"]]
            ),
            "reads": (
                "A prospect is a visitor the room has a recorded page view for, whether or "
                "not the block was shown. Matched visits are the ones that satisfied a "
                "workflow's targeting rules."
            ),
            "as_of": _iso(reference),
            "prospects": prospects,
        }


def _moment(value: Any) -> datetime | None:
    """Read a stored ISO 8601 moment, or None when it is missing or unzoned."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else None


__all__ = ["INTERACTION_KINDS", "MAX_WINDOW_DAYS", "PageOutreach"]
