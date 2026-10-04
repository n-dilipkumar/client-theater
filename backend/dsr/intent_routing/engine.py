"""The engine: one engagement observation in, one alert, task and signal out.

This is the only module in the package that writes. Everything it writes goes
through :class:`~dsr.store.RecordStore`, and every writing call takes ``source`` as
a required keyword so the audit row names the route that served the write. A
hardcoded string inside a domain method is a defect, and that class of bug has
shipped in this codebase before: a feature's audit log kept naming a path the app
had stopped serving.

The chain is the researched data flow, in order and without additions::

    Room and page events
      -> threshold evaluation        (dsr.intent_routing.thresholds)
      -> account and contact resolution   (dsr.intent_routing.resolution)
      -> alert dispatch, email and Slack   (dsr.intent_routing.routing)
      -> CRM task and field update      (this module)
      -> rep action logged                (this module)

Every step reads the record the step before it wrote, so the whole chain is
auditable in order, and a failure at any step leaves the steps before it on the
record rather than rolling them back. A rollback would hide exactly the thing the
audit log exists to show.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dsr.intent_routing import payload as payload_module, resolution, routing
from dsr.intent_routing.errors import (
    DuplicateRule,
    DuplicateWatchlist,
    InvalidAction,
    InvalidWatchlist,
    UnknownAlert,
    UnknownRule,
    UnknownSignal,
    UnknownTask,
    UnknownWatchlist,
    UnresolvedAccount,
)
from dsr.intent_routing.inferences import INFERENCES
from dsr.intent_routing.thresholds import (
    Engagement,
    evaluate,
    parse_engagement,
    threshold_table,
)
from dsr.intent_routing.vocabulary import (
    ACTION_KINDS,
    ACTIONS,
    ALERT_ACKNOWLEDGED,
    ALERT_CONTACTED,
    ALERT_DISMISSED,
    ALERT_OPEN,
    ALERT_STATES,
    ALERTS,
    COLLECTIONS,
    NOTE_REQUIRED_FOR,
    NOTIFICATION_CHANNELS,
    RULES,
    SIGNALS,
    SUPPRESSION_HOURS,
    TASKS,
    THRESHOLDS,
    THRESHOLDS_TO_ALERT,
    WATCHLIST_TIERS,
    WATCHLISTS,
    WINDOW_HOURS,
)
from dsr.store import RecordStore


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str:
    return moment.isoformat() if moment else ""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _data(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("data") or {}


class IntentRouter:
    """Real-time buyer-intent alerting and routing, over one record store."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # ----------------------------------------------------------------- #
    # Published vocabulary and decisions
    # ----------------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every name, number and threshold this workflow uses, served as data."""
        return {
            "ticket": "WF-133",
            "collections": dict(COLLECTIONS),
            "thresholds": threshold_table(),
            "thresholds_to_alert": THRESHOLDS_TO_ALERT,
            "sourced_thresholds": [entry["kind"] for entry in THRESHOLDS if entry["sourced"]],
            "derived_thresholds": [entry["kind"] for entry in THRESHOLDS if not entry["sourced"]],
            "alert_context_fields": list(payload_module.PAYLOAD_FIELDS),
            "notification_channels": list(NOTIFICATION_CHANNELS),
            "primary_channel": "email",
            "dispatch_states": ["queued", "held_for_integration", "skipped", "suppressed"],
            "alert_states": list(ALERT_STATES),
            "action_kinds": list(ACTION_KINDS),
            "note_required_for": list(NOTE_REQUIRED_FOR),
            "watchlist_tiers": list(WATCHLIST_TIERS),
            "routing_rule_kinds": [
                "crm_owner",
                "team",
                "watchlist",
                "fallback",
            ],
            "routing_match_keys": list(routing.MATCH_KEYS),
            "suppression_hours": SUPPRESSION_HOURS,
            "window_hours": WINDOW_HOURS,
            "sends_mail": False,
            "sends_slack": False,
            "company_level_only": True,
            "why_no_vendor": (
                "Reverse-IP company resolution is named by the research as the one piece with no "
                "clean open-source equivalent. This build calls no provider for it. It reads the "
                "company the visitor-identification workflow already identified, and refuses an "
                "account it cannot resolve rather than paying a per-event vendor for a guess."
            ),
        }

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this workflow rests on, and how to change each one."""
        return {"count": len(INFERENCES), "inferences": [dict(e) for e in INFERENCES]}

    # ----------------------------------------------------------------- #
    # Watchlists: who is a target account
    # ----------------------------------------------------------------- #

    def watchlists(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        listed = self.store.list(WATCHLISTS, room_id=room_id, limit=200)
        return [self._watchlist_view(record) for record in listed]

    def _watchlist_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        accounts = [str(entry) for entry in (data.get("accounts") or [])]
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "name": data.get("name", ""),
            "tier": data.get("tier", ""),
            "accounts": accounts,
            "account_count": len(accounts),
            "notify": list(data.get("notify") or []),
            "note": data.get("note", ""),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
        }

    def _normalise_watchlist(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise InvalidWatchlist("a watchlist must be an object")
        name = _text(payload.get("name"))
        if not name:
            raise InvalidWatchlist("name is required")
        tier = _text(payload.get("tier")) or "named"
        if tier not in WATCHLIST_TIERS:
            raise InvalidWatchlist(f"tier must be one of {', '.join(WATCHLIST_TIERS)}")
        raw_accounts = payload.get("accounts") or []
        if isinstance(raw_accounts, str) or not isinstance(raw_accounts, (list, tuple)):
            raise InvalidWatchlist("accounts must be a list of company keys")
        accounts: list[str] = []
        for entry in raw_accounts:
            key = _text(entry)
            if not key:
                raise InvalidWatchlist("accounts must not contain a blank company key")
            if key not in accounts:
                accounts.append(key)
        notify: list[str] = []
        raw_notify = payload.get("notify") or []
        if isinstance(raw_notify, str):
            raw_notify = [raw_notify]
        if not isinstance(raw_notify, (list, tuple)):
            raise InvalidWatchlist("notify must be a list of addresses")
        for entry in raw_notify:
            address = _text(entry).lower()
            if not address:
                raise InvalidWatchlist("notify must not contain a blank address")
            if "@" not in address or address.startswith("@") or address.endswith("@"):
                raise InvalidWatchlist(
                    f"{address!r} is not an email address. Only email is a deliverable channel "
                    "in this build."
                )
            if address not in notify:
                notify.append(address)
        return {
            "name": name,
            "tier": tier,
            "accounts": accounts,
            "notify": notify,
            "note": _text(payload.get("note")),
        }

    def create_watchlist(
        self, payload: Any, *, room_id: str | None, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Save a target-account watchlist in a room."""
        data = self._normalise_watchlist(payload)
        for existing in self.store.list(WATCHLISTS, room_id=room_id, limit=200):
            if _text(_data(existing).get("name")).lower() == data["name"].lower():
                raise DuplicateWatchlist(
                    f"a watchlist called {data['name']!r} already exists in this room"
                )
        record = self.store.create(WATCHLISTS, data, room_id=room_id, actor=actor, source=source)
        return self._watchlist_view(record)

    def read_watchlist(self, watchlist_id: str) -> dict[str, Any]:
        record = self.store.get(watchlist_id)
        if record is None or record.get("collection") != WATCHLISTS:
            raise UnknownWatchlist(f"no watchlist exists under {watchlist_id!r}")
        return self._watchlist_view(record)

    def delete_watchlist(
        self, watchlist_id: str, *, actor: str | None, source: str
    ) -> dict[str, Any]:
        record = self.store.get(watchlist_id)
        if record is None or record.get("collection") != WATCHLISTS:
            raise UnknownWatchlist(f"no watchlist exists under {watchlist_id!r}")
        self.store.delete(watchlist_id, actor=actor, source=source)
        return {
            "id": watchlist_id,
            "deleted": True,
            "accounts": len(_data(record).get("accounts") or []),
        }

    def watchlist_for(self, *, room_id: str, company_key: str) -> dict[str, Any] | None:
        """The watchlist in a room that watches a company, if any.

        Read by scanning rather than by filtering, and the reason is a property of
        the store rather than a preference. ``accounts`` is a JSON **array**, and the
        dynamic index gives an array member a positional path, so ``accounts.0`` and
        ``accounts.1`` are the indexable paths and "is this company on the list"
        is not one of them. A room holds a handful of watchlists, so the scan is
        cheap and it is the only correct way to ask.
        """
        for record in self.store.list(WATCHLISTS, room_id=room_id, limit=200):
            data = _data(record)
            if company_key in [str(entry) for entry in (data.get("accounts") or [])]:
                return self._watchlist_view(record)
        return None

    # ----------------------------------------------------------------- #
    # Routing rules: who hears
    # ----------------------------------------------------------------- #

    def rules(self, *, room_id: str | None = None) -> list[dict[str, Any]]:
        return [
            self._rule_view(record) for record in self.store.list(RULES, room_id=room_id, limit=200)
        ]

    def _rule_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "name": data.get("name", ""),
            "kind": data.get("kind", ""),
            "match": dict(data.get("match") or {}),
            "notify": list(data.get("notify") or []),
            "position": data.get("position", 0),
            "stop": bool(data.get("stop", True)),
            "note": data.get("note", ""),
            "created_at": record.get("created_at"),
            "revision": record.get("revision"),
        }

    def _fallback_count(self, room_id: str | None) -> int:
        return sum(
            1
            for record in self.store.list(RULES, room_id=room_id, limit=200)
            if _text(_data(record).get("kind")) == "fallback"
        )

    def create_rule(
        self, payload: Any, *, room_id: str | None, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Save one link in the alert routing chain."""
        data = routing.normalise_rule(payload, fallback_count=self._fallback_count(room_id))
        for existing in self.store.list(RULES, room_id=room_id, limit=200):
            if _text(_data(existing).get("name")).lower() == data["name"].lower():
                raise DuplicateRule(f"a routing rule called {data['name']!r} already exists here")
        record = self.store.create(RULES, data, room_id=room_id, actor=actor, source=source)
        return self._rule_view(record)

    def read_rule(self, rule_id: str) -> dict[str, Any]:
        record = self.store.get(rule_id)
        if record is None or record.get("collection") != RULES:
            raise UnknownRule(f"no routing rule exists under {rule_id!r}")
        return self._rule_view(record)

    def delete_rule(self, rule_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        record = self.store.get(rule_id)
        if record is None or record.get("collection") != RULES:
            raise UnknownRule(f"no routing rule exists under {rule_id!r}")
        self.store.delete(rule_id, actor=actor, source=source)
        return {"id": rule_id, "deleted": True, "kind": _data(record).get("kind", "")}

    # ----------------------------------------------------------------- #
    # Evaluation: report a decision without writing it
    # ----------------------------------------------------------------- #

    def evaluate(self, payload: Any, *, now: datetime | None = None) -> dict[str, Any]:
        """Say what the thresholds decide, and write nothing.

        The report and the commit are two routes on purpose. A buyer who watched
        40 percent of a video is a fact, not a caller error, so a caller watching
        several companies before deciding who to alert gets its decision without
        creating six alerts and suppressing five of them.
        """
        reference = _now(now)
        engagement = parse_engagement(payload, now=reference)
        assessed = evaluate(engagement, now=reference)
        account = resolution.resolve(self.store, assessed.company_key, room_id=assessed.room_id)
        watchlist = self.watchlist_for(room_id=assessed.room_id, company_key=assessed.company_key)
        crossings = [entry.as_dict() for entry in assessed.crossed]
        return {
            "company_key": assessed.company_key,
            "room_id": assessed.room_id,
            "on_watchlist": watchlist is not None,
            "watchlist": watchlist["name"] if watchlist else "",
            "account": account.to_dict(),
            "raised": assessed.qualifies,
            "reason": self._reason(assessed, account, watchlist),
            "signal": assessed.to_dict(),
            "crossed": crossings,
            "thresholds_to_alert": THRESHOLDS_TO_ALERT,
            "wrote": False,
        }

    @staticmethod
    def _reason(engagement: Engagement, account: Any, watchlist: dict[str, Any] | None) -> str:
        if not engagement.crossed:
            measured = ", ".join(
                f"{key} {value}" for key, value in sorted(engagement.measurements().items())
            )
            return f"no threshold was met. Measured: {measured}."
        if watchlist is None:
            return (
                f"{len(engagement.crossed)} threshold(s) met, but {engagement.company_key} is on no "
                "watchlist in this room, so it is not a target account."
            )
        if not account.resolved:
            return (
                f"{len(engagement.crossed)} threshold(s) met and the account is on "
                f"{watchlist['name']}, but no opportunity could be attached, so there is nowhere "
                "for the follow-up task to hang."
            )
        return (
            f"{len(engagement.crossed)} threshold(s) met on a target account with an opportunity."
        )

    # ----------------------------------------------------------------- #
    # Ingestion: the whole chain, committed
    # ----------------------------------------------------------------- #

    def ingest(
        self,
        payload: Any,
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Evaluate, resolve, route, alert, and create the follow-up task.

        Refuses with 404 for a company nobody has identified and 409 for an account
        that cannot be routed, rather than writing a partial chain. Both refusals
        are recorded in :mod:`dsr.intent_routing.resolution` as the deliberate
        consequence of not calling a paid reverse-IP provider.
        """
        reference = _now(now)
        engagement = parse_engagement(payload, now=reference)
        scoped_room = room_id or engagement.room_id
        assessed = evaluate(engagement, now=reference)

        account = resolution.resolve(self.store, assessed.company_key, room_id=scoped_room)
        watchlist = self.watchlist_for(room_id=scoped_room, company_key=assessed.company_key)
        crossings = [entry.as_dict() for entry in assessed.crossed]

        if not assessed.qualifies:
            return {
                "raised": False,
                "wrote": False,
                "reason": self._reason(assessed, account, watchlist),
                "signal": assessed.to_dict(),
                "crossed": crossings,
                "company_key": assessed.company_key,
                "room_id": scoped_room,
            }
        if watchlist is None:
            return {
                "raised": False,
                "wrote": False,
                "reason": self._reason(assessed, account, watchlist),
                "signal": assessed.to_dict(),
                "crossed": crossings,
                "company_key": assessed.company_key,
                "room_id": scoped_room,
            }
        if not account.resolved:
            raise UnresolvedAccount(
                f"{len(assessed.crossed)} threshold(s) were met for {assessed.company_key}, but no "
                "opportunity is on file for it in this room. The researched flow ends with a "
                "follow-up task on the opportunity, so this alert would have nowhere to go."
            )

        stakeholder = resolution.stakeholder_of(assessed, account)
        addresses = self._address_candidates(scoped_room, account, watchlist)
        suppression = routing.suppressed_until(
            self.store,
            room_id=scoped_room,
            company_key=assessed.company_key,
            addresses=addresses,
            now=reference,
        )
        recipients = routing.recipients_for(
            self._rule_rows(scoped_room),
            account,
            watchlist=watchlist,
            room_default=(watchlist.get("notify") or [""])[0] if watchlist.get("notify") else "",
            watchlist_notify=list(watchlist.get("notify") or []),
        )
        dispatches = [
            routing.dispatch_for(channel, recipients["recipients"])
            for channel in NOTIFICATION_CHANNELS
        ]
        if suppression["suppressed"]:
            dispatches = [
                {**entry, "state": "suppressed", "reason": suppression["reason"]}
                for entry in dispatches
            ]
        context = payload_module.payload_for(assessed, account, stakeholder, crossings)
        room_label = assessed.room_label
        subject = payload_module.subject_for(context, room_label)
        body = payload_module.body_for(context, room_label)

        signal = self.store.create(
            SIGNALS,
            {
                "company_key": assessed.company_key,
                "room_id": scoped_room,
                "stakeholder": stakeholder,
                "pages": list(assessed.pages),
                "measurements": assessed.measurements(),
                "crossed": crossings,
                "window_hours": assessed.window_hours,
                "last_seen_at": _iso(assessed.last_seen_at),
                "raised_at": reference.isoformat(),
                "watchlist_id": watchlist["id"],
                "watchlist_name": watchlist["name"],
                "accountable": account.accountable,
                "matched": account.matched,
                "opportunity_id": account.opportunity_id,
                "qualifies": True,
            },
            room_id=scoped_room,
            actor=actor,
            source=source,
        )

        alert = self.store.create(
            ALERTS,
            {
                "signal_id": signal["id"],
                "company_key": assessed.company_key,
                "room_id": scoped_room,
                "accountable": account.accountable,
                "recipients": recipients["recipients"],
                "consulted": recipients["consulted"],
                "dispatches": dispatches,
                "channel_states": routing.channel_states(dispatches),
                "suppressed": bool(suppression["suppressed"]),
                "suppression": suppression,
                "payload": context,
                "subject": subject,
                "body": body,
                "dispatched_at": reference.isoformat() if not suppression["suppressed"] else "",
                "state": ALERT_OPEN,
                "actions": [],
                "opportunity_id": account.opportunity_id,
                "crm_system": account.crm_system,
            },
            room_id=scoped_room,
            actor=actor,
            source=source,
        )

        task = self.store.create(
            TASKS,
            {
                "signal_id": signal["id"],
                "alert_id": alert["id"],
                "company_key": assessed.company_key,
                "room_id": scoped_room,
                "opportunity_id": account.opportunity_id,
                "accountable": account.accountable,
                "crm_system": account.crm_system,
                "title": subject,
                "context": context,
                "due_at": (reference + timedelta(hours=SUPPRESSION_HOURS)).isoformat(),
                "state": "open",
                "created_at": reference.isoformat(),
            },
            room_id=scoped_room,
            actor=actor,
            source=source,
        )

        return {
            "raised": True,
            "wrote": True,
            "reason": self._reason(assessed, account, watchlist),
            "company_key": assessed.company_key,
            "room_id": scoped_room,
            "signal": self._signal_view(signal),
            "alert": self._alert_view(alert),
            "task": self._task_view(task),
            "crossed": crossings,
        }

    def _address_candidates(
        self, room_id: str, account: Any, watchlist: dict[str, Any]
    ) -> list[str]:
        """Every address that could already have been told about this account.

        The candidate set is deliberately wider than the delivered one. Suppression
        asks "has anybody already been told about this account", and the address
        that answered that question last time may not be the address the chain picks
        today, so narrowing the set to today's recipients would let the same person
        be alerted twice in a day through two different rules.
        """
        candidates: list[str] = []
        if account.owner_email:
            candidates.append(account.owner_email)
        candidates.extend(watchlist.get("notify") or [])
        recipients = routing.recipients_for(
            self._rule_rows(room_id),
            account,
            watchlist=watchlist,
            room_default=(watchlist.get("notify") or [""])[0] if watchlist.get("notify") else "",
            watchlist_notify=list(watchlist.get("notify") or []),
        )
        for entry in recipients["recipients"]:
            address = _text(entry.get("address"))
            if address and address not in candidates:
                candidates.append(address)
        return candidates

    def _rule_rows(self, room_id: str | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.store.list(RULES, room_id=room_id, limit=200):
            rows.append({**_data(record), "id": record.get("id")})
        return rows

    # ----------------------------------------------------------------- #
    # Reading the chain back
    # ----------------------------------------------------------------- #

    def _signal_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "company_key": data.get("company_key", ""),
            "stakeholder": data.get("stakeholder", {}),
            "pages": list(data.get("pages") or []),
            "measurements": data.get("measurements", {}),
            "crossed": list(data.get("crossed") or []),
            "window_hours": data.get("window_hours", WINDOW_HOURS),
            "last_seen_at": data.get("last_seen_at", ""),
            "raised_at": data.get("raised_at", ""),
            "watchlist_name": data.get("watchlist_name", ""),
            "accountable": data.get("accountable", ""),
            "matched": data.get("matched", ""),
            "opportunity_id": data.get("opportunity_id", ""),
            "created_at": record.get("created_at"),
            "revision": record.get("revision"),
        }

    def _alert_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "signal_id": data.get("signal_id", ""),
            "company_key": data.get("company_key", ""),
            "accountable": data.get("accountable", ""),
            "recipients": list(data.get("recipients") or []),
            "consulted": list(data.get("consulted") or []),
            "dispatches": list(data.get("dispatches") or []),
            "channel_states": data.get("channel_states", {}),
            "suppressed": bool(data.get("suppressed")),
            "suppression": data.get("suppression", {}),
            "payload": data.get("payload", {}),
            "subject": data.get("subject", ""),
            "body": data.get("body", ""),
            "dispatched_at": data.get("dispatched_at", ""),
            "state": data.get("state", ALERT_OPEN),
            "actions": list(data.get("actions") or []),
            "opportunity_id": data.get("opportunity_id", ""),
            "crm_system": data.get("crm_system", ""),
            "created_at": record.get("created_at"),
            "revision": record.get("revision"),
        }

    def _task_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "signal_id": data.get("signal_id", ""),
            "alert_id": data.get("alert_id", ""),
            "company_key": data.get("company_key", ""),
            "opportunity_id": data.get("opportunity_id", ""),
            "accountable": data.get("accountable", ""),
            "crm_system": data.get("crm_system", ""),
            "title": data.get("title", ""),
            # The payload travels on the task rather than beside it. A rep looking at
            # the task in a CRM should not have to open the room to learn which pages
            # the buyer read, and a task that has to be joined back to an alert is a
            # task nobody reads.
            "context": data.get("context", {}),
            "due_at": data.get("due_at", ""),
            "state": data.get("state", "open"),
            "created_at": data.get("created_at"),
        }

    def _action_view(self, record: dict[str, Any]) -> dict[str, Any]:
        data = _data(record)
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "signal_id": data.get("signal_id", ""),
            "alert_id": data.get("alert_id", ""),
            "company_key": data.get("company_key", ""),
            "kind": data.get("kind", ""),
            "note": data.get("note", ""),
            "actor": data.get("actor", ""),
            "at": data.get("at", ""),
            "resulting_state": data.get("resulting_state", ALERT_OPEN),
        }

    def signals(
        self, *, room_id: str | None = None, company_key: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        records = self.store.list(SIGNALS, room_id=room_id, limit=limit)
        if company_key:
            records = [r for r in records if _data(r).get("company_key") == company_key]
        return [self._signal_view(record) for record in records]

    def read_signal(self, signal_id: str) -> dict[str, Any]:
        record = self.store.get(signal_id)
        if record is None or record.get("collection") != SIGNALS:
            raise UnknownSignal(f"no intent signal exists under {signal_id!r}")
        return self._signal_view(record)

    def alerts(
        self, *, room_id: str | None = None, state: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        records = self.store.list(ALERTS, room_id=room_id, limit=limit)
        if state:
            records = [r for r in records if _data(r).get("state") == state]
        return [self._alert_view(record) for record in records]

    def read_alert(self, alert_id: str) -> dict[str, Any]:
        record = self.store.get(alert_id)
        if record is None or record.get("collection") != ALERTS:
            raise UnknownAlert(f"no alert exists under {alert_id!r}")
        return self._alert_view(record)

    def tasks(
        self, *, room_id: str | None = None, company_key: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        records = self.store.list(TASKS, room_id=room_id, limit=limit)
        if company_key:
            records = [r for r in records if _data(r).get("company_key") == company_key]
        return [self._task_view(record) for record in records]

    def read_task(self, task_id: str) -> dict[str, Any]:
        record = self.store.get(task_id)
        if record is None or record.get("collection") != TASKS:
            raise UnknownTask(f"no follow-up task exists under {task_id!r}")
        return self._task_view(record)

    def actions(self, *, room_id: str | None = None, signal_id: str = "") -> list[dict[str, Any]]:
        """The rep action log, oldest first.

        Oldest first is the one place this feature reads a collection in the
        opposite order to the others. Every other list is newest first because a
        screen wants the most recent row at the top. A log is not a list of
        rows to choose between, it is a sequence a rep reads in the order it
        happened, and reversing it makes "acknowledged then contacted" read as
        "contacted then acknowledged".
        """
        records = self.store.list(ACTIONS, room_id=room_id, limit=500, descending=False)
        if signal_id:
            records = [r for r in records if _data(r).get("signal_id") == signal_id]
        return [self._action_view(record) for record in records]

    # ----------------------------------------------------------------- #
    # Who is engaged and who is not
    # ----------------------------------------------------------------- #

    def engagement(
        self, *, room_id: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Every target account in a room, and whether it has an open alert.

        This is the researched surface with the least definition in it: "who is
        engaged and who is not" is named as a product surface and the research says
        nothing about what it shows. The derived reading is that the list is the
        **watchlist**, not the set of accounts that happened to trigger something.
        A view built from the alerts alone can only ever show who is engaged, and
        the half of the screen the research asks for by name would be empty.

        So an account is engaged when it has an alert that no rep has dismissed, and
        not engaged otherwise. The account that has never been seen appears in the
        list with an empty last-seen moment, which is the information a seller most
        needs and the one an alert-driven view can never give them.
        """
        reference = _now(now)
        accounts: dict[str, dict[str, Any]] = {}
        for watchlist in self.watchlists(room_id=room_id):
            for key in watchlist["accounts"]:
                row = accounts.setdefault(
                    key,
                    {
                        "company_key": key,
                        "company_name": "",
                        "watchlist": watchlist["name"],
                        "tier": watchlist["tier"],
                        "opportunity_id": "",
                        "accountable": "",
                        "last_seen_at": "",
                        "pages": [],
                        "alert_id": "",
                        "state": "",
                    },
                )
                if not row["watchlist"]:
                    row["watchlist"] = watchlist["name"]
                    row["tier"] = watchlist["tier"]

        for signal in self.signals(room_id=room_id):
            key = str(signal["company_key"])
            row = accounts.setdefault(
                key,
                {
                    "company_key": key,
                    "company_name": "",
                    "watchlist": "",
                    "tier": "",
                    "opportunity_id": "",
                    "accountable": "",
                    "last_seen_at": "",
                    "pages": [],
                    "alert_id": "",
                    "state": "",
                },
            )
            row["last_seen_at"] = max(row["last_seen_at"], str(signal["last_seen_at"] or ""))
            row["pages"] = list(signal["pages"])
            row["accountable"] = signal["accountable"]
            row["opportunity_id"] = signal["opportunity_id"]
            row["signal_id"] = signal["id"]

        for alert in self.alerts(room_id=room_id):
            key = str(alert["company_key"])
            row = accounts.get(key)
            if row is None:
                continue
            if alert["state"] != ALERT_DISMISSED:
                row["alert_id"] = alert["id"]
                row["state"] = alert["state"]

        rows: list[dict[str, Any]] = []
        for key in sorted(accounts):
            row = accounts[key]
            company = resolution.find_company(self.store, key)
            data = _data(company) if company else {}
            row["company_name"] = str(data.get("name") or "")
            row["engaged"] = bool(row["alert_id"])
            row["engagement"] = "engaged" if row["engaged"] else "not_engaged"
            row["hours_since_seen"] = _hours_since(row["last_seen_at"], reference)
            rows.append(row)

        return {
            "room_id": room_id,
            "accounts": rows,
            "count": len(rows),
            "engaged": sum(1 for row in rows if row["engaged"]),
            "not_engaged": sum(1 for row in rows if not row["engaged"]),
            "window_hours": WINDOW_HOURS,
            "reads": (
                "Engaged means the account has an alert that no rep has dismissed. The list is the "
                "watchlist, so an account nobody has engaged still appears."
            ),
        }

    # ----------------------------------------------------------------- #
    # Rep action logged
    # ----------------------------------------------------------------- #

    def log_action(
        self,
        signal_id: str,
        payload: Any,
        *,
        actor: str | None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Record what a rep did about a signal, and move the alert's state.

        The decision, stated: **the workflow records and does not decide.** The
        research ends its data flow at "rep action logged" and nothing follows it,
        so there is no next step for this workflow to own. What it does own is the
        log, and the alert's state is derived from it rather than set directly, so
        a rep cannot set a state the log does not support.

        A dismissal sticks. Once an alert is dismissed it stays dismissed even if a
        later action says acknowledged, because "I decided this was not worth a
        call" is not undone by "I looked at it again".
        """
        reference = _now(now)
        if not isinstance(payload, dict):
            raise InvalidAction("a rep action must be an object")
        kind = _text(payload.get("kind"))
        if kind not in ACTION_KINDS:
            raise InvalidAction(
                f"kind must be one of {', '.join(ACTION_KINDS)}, got {kind or 'nothing'}"
            )
        note = _text(payload.get("note"))
        if kind in NOTE_REQUIRED_FOR and not note:
            raise InvalidAction(
                f"a {kind} needs a note. Without one the log cannot answer which alerts were not "
                "worth a call, which is the question it exists for."
            )

        signal = self.store.get(signal_id)
        if signal is None or signal.get("collection") != SIGNALS:
            raise UnknownSignal(f"no intent signal exists under {signal_id!r}")
        alert_id = str(_data(signal).get("alert_id") or "")
        alert_record = self.store.get(alert_id) if alert_id else None
        if alert_record is None:
            for candidate in self.store.list(ALERTS, room_id=signal.get("room_id"), limit=500):
                if _data(candidate).get("signal_id") == signal_id:
                    alert_record = candidate
                    break
        if alert_record is None:
            raise UnknownAlert(f"signal {signal_id!r} has no alert to record an action against")

        existing = list(_data(alert_record).get("actions") or [])
        resulting = _next_state(str(_data(alert_record).get("state") or ALERT_OPEN), kind)
        entry = {
            "signal_id": signal_id,
            "alert_id": alert_record["id"],
            "company_key": _data(signal).get("company_key", ""),
            "room_id": signal.get("room_id"),
            "kind": kind,
            "note": note,
            "actor": actor or "",
            "at": reference.isoformat(),
            "resulting_state": resulting,
        }
        record = self.store.create(
            ACTIONS, entry, room_id=signal.get("room_id"), actor=actor, source=source
        )
        self.store.update(
            alert_record["id"],
            {
                "actions": existing
                + [{"kind": kind, "note": note, "actor": entry["actor"], "at": entry["at"]}],
                "state": resulting,
            },
            actor=actor,
            source=source,
        )
        return {
            "action": self._action_view(record),
            "alert": self._alert_view(self.store.require(alert_record["id"])),
            "state": resulting,
        }


def _next_state(current: str, kind: str) -> str:
    """The alert's state after one more rep action.

    A dismissal is terminal. Every other kind moves the alert forward, and a kind
    that would move it backwards leaves it where it is, so the log stays monotonic
    and the state on the alert is always the furthest point the rep reached.
    """
    if current == ALERT_DISMISSED:
        return ALERT_DISMISSED
    if kind == "dismissed":
        return ALERT_DISMISSED
    if kind == "contacted":
        return ALERT_CONTACTED
    if kind == "acknowledged":
        return ALERT_ACKNOWLEDGED
    if current in (ALERT_ACKNOWLEDGED, ALERT_CONTACTED):
        return current
    return ALERT_OPEN


def _hours_since(moment: str, reference: datetime) -> int | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(moment)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((reference - parsed).total_seconds() // 3600))


__all__ = ["IntentRouter"]
