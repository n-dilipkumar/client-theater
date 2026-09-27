"""The one object this workflow is driven through.

:class:`SignalEngine` is the façade the HTTP layer calls. It holds nothing but a
:class:`~dsr.store.RecordStore` handle, which is why the feature module builds one
per request from ``StoreDep`` rather than hanging it on ``app.state``: an
``app.state`` entry would mean editing ``dsr/api.py``, and the whole point of the
feature host is that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration.
The audit row is the product's guarantee, and an audit row that names a string
rather than a route cannot be traced back to the request that caused it - which is
a defect this codebase has already shipped once. A required keyword means the
omission is a ``TypeError`` at the call site rather than a silently untraceable
row in production.

Two collections, both schema-flexible
-------------------------------------
``signal_registration`` and ``intent_signal``. Neither has a migration, a typed
column, or a required field beyond the researched contract, because a team adding
a field must not need to coordinate with anyone. Every filter goes through
``find()`` and therefore through the dynamic index, so a new field is queryable
the moment it is written.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from dsr.signals import emission as emission_module
from dsr.signals import feed as feed_module
from dsr.signals import icume
from dsr.signals import indicators as indicators_module
from dsr.signals import registration as registration_module
from dsr.signals import schema
from dsr.signals.errors import (
    DuplicateSignalType,
    ImmutableContractError,
    SignalError,
    UnregisteredSignalType,
)
from dsr.signals.vocabulary import (
    ACTIONABILITY_NOTE,
    ATTRIBUTION_TYPES,
    URGENCIES,
    describe as describe_vocabulary,
    require_urgency,
)
from dsr.store import RecordStore

#: The two collections this workflow owns. Named here so a filter and a route
#: cannot disagree about which one they mean.
REGISTRATIONS = "signal_registration"
SIGNALS = "intent_signal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class SignalEngine:
    """Register signal types, emit signals, and assemble the Live Feed."""

    def __init__(self, store: RecordStore, *, now: Any = None) -> None:
        self.store = store
        # Injectable so a test can assert on ordering and timestamps without
        # freezing the whole process clock.
        self._now = now or _now

    # -- vocabulary --------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data.

        A client renders its pickers from this rather than from a list compiled
        into the page, so a value added server-side reaches every client at once.
        """
        return {
            **describe_vocabulary(),
            **indicators_module.describe_vocabulary(),
            "json_schema_keywords": sorted(schema.SUPPORTED_KEYWORDS),
            "icu": {
                "supported_argument_types": list(icume.SUPPORTED_ARGUMENT_TYPES),
                "note": (
                    "A localized description is an ICU Message. This renderer implements the "
                    "simple argument, plural (with =N exact branches and #) and select, which "
                    "is what the research's own worked example uses. Apostrophe escaping and "
                    "the number/date/time argument types are not implemented; see "
                    "dsr/signals/icume.py."
                ),
            },
        }

    def inferences(self) -> dict[str, Any]:
        """Where this workflow stops being sourced. See :mod:`dsr.signals.inferences`."""
        from dsr.signals import inferences as inferences_module

        return inferences_module.describe()

    # -- registrations ------------------------------------------------------ #

    def _find_registration(
        self, *, type_name: str | None = None, integration_id: str | None = None
    ) -> dict[str, Any] | None:
        """The live registration for a (integration, type) pair.

        "Per integration, the signal type can only be registered once", so the
        pair is the key. An emission that names no integration looks across every
        integration, which is legitimate - two integrations may each register the
        same type - so the most recently registered one wins and the response
        says which integration it resolved to.
        """
        if not type_name:
            return None
        where: dict[str, Any] = {"type": type_name}
        if integration_id is not None:
            where["integration_id"] = integration_id
        found = self.store.find(REGISTRATIONS, where, limit=1000)
        return found[0] if found else None

    def register(self, payload: Mapping[str, Any], *, actor: str | None, source: str) -> dict[str, Any]:
        """Register a signal type, once per integration.

        The one-per-integration rule is a researched property, so the check is
        here rather than left to the caller. It is a find-then-create rather than
        a unique index, and that is sound here for one reason: this product has a
        single writer behind one lock (``AuditedDatabase`` is documented as
        one-writer), so no other request can insert between the find and the
        create. Under a second writer it would stop being true, which is why the
        finding would be worth revisiting if that ever changes.
        """
        record = registration_module.normalise_registration(payload)
        idempotency_key = record.get("idempotency_key")

        if idempotency_key:
            existing = self.store.find(REGISTRATIONS, {"idempotency_key": idempotency_key}, limit=1)
            if existing:
                # The first registration of a type wins, exactly as the first
                # signal with an idempotency key does. Answering with the record
                # that is already there is what makes a retried registration
                # safe rather than a duplicate contract.
                return {
                    "outcome": "already_registered",
                    "reason": "duplicate_idempotency_key",
                    "registration": self.present(existing[0]),
                }

        clash = self._find_registration(
            type_name=record["type"], integration_id=record["integration_id"]
        )
        if clash is not None:
            raise DuplicateSignalType(
                f"signal type {record['type']!r} is already registered on integration "
                f"{record['integration_id']!r} (registration {clash['id']}). The research "
                "states that per integration the signal type can only be registered once, "
                "because two registrations of one type would give that type two incompatible "
                "shapes. Amend the existing registration instead - additive changes only."
            )

        created = self.store.create(REGISTRATIONS, record, actor=actor, source=source)
        return {"outcome": "registered", "registration": self.present(created)}

    def present(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A registration flattened to ``data`` plus the record id and timestamps.

        Every read of a registration goes through this, and so does every internal
        use of one, so there is exactly one shape for a registration in this
        package. Handing the raw envelope to the domain would put every field
        lookup one level too deep, which is the kind of mistake that returns a
        silent ``None`` rather than an error.
        """
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
            **{key: value for key, value in data.items()},
        }

    def registrations(
        self,
        *,
        integration_id: str | None = None,
        type_name: str | None = None,
        include_withdrawn: bool = False,
    ) -> list[dict[str, Any]]:
        """The registry, newest first.

        A withdrawn registration is a soft-deleted row, so it is out of the
        default listing and reachable with ``include_withdrawn``; the signals that
        were emitted against it still name it, which is why a withdrawn
        registration is not destroyed.
        """
        if integration_id:
            records = self.store.find(
                REGISTRATIONS,
                {"integration_id": integration_id},
                limit=1000,
                include_deleted=include_withdrawn,
            )
        elif type_name:
            # A listing filtered by type returns every match, not one of them.
            # Two integrations may each register the same type - the
            # one-per-integration rule says so - and a listing that silently
            # showed one of them would look like the other was never registered.
            # The "first one wins" resolution belongs to emitting, not to reading.
            records = self.store.find(
                REGISTRATIONS, {"type": type_name}, limit=1000, include_deleted=include_withdrawn
            )
        else:
            records = self.store.list(REGISTRATIONS, limit=1000, include_deleted=include_withdrawn)
        if not include_withdrawn:
            records = [record for record in records if record.get("deleted_at") is None]
        return [self.present(record) for record in records]

    def registration(self, registration_id: str) -> dict[str, Any] | None:
        record = self.store.get(registration_id)
        if record is None or record.get("collection") != REGISTRATIONS:
            return None
        return record

    def amend(
        self, registration_id: str, patch: Mapping[str, Any], *, actor: str | None, source: str
    ) -> dict[str, Any]:
        """Apply an additive amendment, or refuse it and say every reason why.

        "Only additive changes will be allowed" has to become a decision, and the
        decision is the invalidation test in
        :func:`dsr.signals.registration.amendment_findings`. Every offending path
        comes back at once, because a partner correcting a rejected amendment
        should not have to resubmit once per field.
        """
        record = self.registration(registration_id)
        if record is None:
            raise SignalError(f"registration {registration_id} not found")
        current = dict(record.get("data") or {})
        findings = registration_module.amendment_findings(current, patch)
        if findings:
            raise ImmutableContractError(
                "a registered signal type is an immutable contract and only additive changes "
                "are allowed. Refused: "
                + "; ".join(
                    f"{finding['path']} ({finding['change']}): {finding['detail']}"
                    for finding in findings
                )
            )
        merged = registration_module.apply_amendment(current, patch)
        updated = self.store.update(registration_id, merged, actor=actor, source=source)
        return self.present(updated)
    def withdraw(self, registration_id: str, *, actor: str | None, source: str) -> dict[str, Any]:
        """Withdraw a registration that has not been used.

        A registration that has already emitted signals is not withdrawable.
        "Globally installed signals should be considered an immutable API contract
        with Salesloft": a contract with signals already delivered under it cannot
        be retracted, and quietly hiding it would leave every signal that names it
        pointing at something the API no longer serves. That refusal is a reading
        of the sentence rather than a quotation from it, and it is recorded as the
        ``withdraw-used-registration`` inference.
        """
        record = self.registration(registration_id)
        if record is None:
            raise SignalError(f"registration {registration_id} not found")
        used = self.store.find(SIGNALS, {"registration_id": registration_id}, limit=1000)
        if used:
            raise ImmutableContractError(
                f"registration {registration_id} has already delivered {len(used)} signal(s) "
                "and cannot be withdrawn. A registered signal type is a contract, and a "
                "contract with signals already delivered under it is not retracted. Add a new "
                "signal type for the new behaviour instead."
            )
        deleted = self.store.delete(registration_id, actor=actor, source=source)
        return {"withdrawn": True, "id": deleted["id"], "type": record["data"].get("type")}

    # -- emission ----------------------------------------------------------- #

    def emit(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Emit one live signal, or drop it because the first one won.

        The researched field list arrives on ``payload``; the idempotency rule is
        applied here, after validation, because "the first one wins" has to mean
        the first *valid* one. A malformed retry must not be able to occupy an
        idempotency key and then block the real signal behind it.
        """
        fields = emission_module.canonical_emission(payload)
        type_name = fields.get("type")
        if not isinstance(type_name, str) or not type_name.strip():
            raise SignalError("type is required: it names the signal registration to emit against")
        integration_id = fields.get("integration_id")

        registration = self._find_registration(
            type_name=type_name.strip(), integration_id=integration_id
        )
        if registration is None:
            where = f" on integration {integration_id!r}" if integration_id else " on any integration"
            raise UnregisteredSignalType(
                f"no live signal registration of type {type_name.strip()!r}{where}. A signal "
                "must follow the structure defined on its registration, and the research has "
                "the integration register the signal type first."
            )

        data = emission_module.normalise_emission(
            payload, self.present(registration), room=self._room(room_id)
        )

        kept = self.store.find(SIGNALS, {"idempotency_key": data["idempotency_key"]}, limit=1)
        if kept:
            winner = kept[0]
            attempts = int((winner.get("data") or {}).get("duplicate_attempts") or 0) + 1
            # The increment is the visible half of "the first one wins": a sender
            # that retried can see that its retry landed and was ignored, and the
            # write is audited against the route that made it.
            self.store.update(
                winner["id"], {"duplicate_attempts": attempts}, actor=actor, source=source
            )
            fresh = self.store.get(winner["id"]) or winner
            return {
                "outcome": "dropped",
                "reason": "duplicate_idempotency_key",
                "detail": (
                    "A signal with this idempotency_key was already delivered, so this one was "
                    "dropped. The research is explicit that the first one wins."
                ),
                "duplicate_attempts": attempts,
                "signal": self.present_signal(fresh),
            }

        record = self.store.create(SIGNALS, data, room_id=room_id, actor=actor, source=source)
        return {
            "outcome": "emitted",
            "reason": None,
            "duplicate_attempts": 0,
            "signal": self.present_signal(record),
        }

    def interact(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Classify a raw DSR interaction, then emit a signal if it qualifies.

        The researched flow is "On each qualifying DSR interaction, emit a live
        signal", which is two decisions, and this method makes both visible:

        * which of the registration's indicators this interaction matched, and for
          each one *why* - so an interaction that fired nothing can say which bound
          it failed rather than returning an empty answer;
        * whether the indicator's bound was actually checked or accepted on trust,
          which travels onto the signal itself.

        Nothing is raised for a non-qualifying interaction. A refused emission on
        :meth:`emit` is the strict researched API; this route is where the
        decision belongs, and a 400 for "the buyer watched 40% of the video" would
        be a caller error when it is a fact about the buyer.
        """
        if not isinstance(payload, Mapping):
            raise SignalError("an interaction must be a JSON object")
        fields = emission_module.canonical_emission(payload)

        type_name = fields.get("type")
        if not isinstance(type_name, str) or not type_name.strip():
            raise SignalError("type is required: it names the signal registration to match against")
        registration = self._find_registration(
            type_name=type_name.strip(), integration_id=fields.get("integration_id")
        )
        if registration is None:
            where = (
                f" on integration {fields.get('integration_id')!r}"
                if fields.get("integration_id")
                else " on any integration"
            )
            raise UnregisteredSignalType(
                f"no live signal registration of type {type_name.strip()!r}{where}, so there is "
                "no indicator to match this interaction against."
            )

        observations = fields.get("observations")
        if observations is None:
            # An indicator's metadata is the interaction's evidence, so an
            # interaction that carries none is asked about explicitly rather than
            # silently matched against an empty observation. `metadata` is the
            # researched spelling of the same thing and is folded onto it.
            observations = fields.get("metadata")
        if observations is None:
            observations = {}
        if not isinstance(observations, Mapping):
            raise SignalError("observations must be an object of measured value to name")

        declared = list(self.present(registration).get("indicators") or [])
        by_key = {str(entry.get("key")): entry for entry in declared}
        decisions = indicators_module.evaluate_all(declared, observations)
        qualified = [decision for decision in decisions if decision["qualifies"]]

        reported = [
            {
                "key": decision["key"],
                "qualifies": decision["qualifies"],
                "reason": decision["reason"],
                "meaning": indicators_module.QUALIFICATION_REASONS[decision["reason"]],
                "checked": decision["reason"] not in (
                    "no_bound_claimed",
                    "bound_unresolvable",
                    "bound_unverifiable",
                ),
                "claim": decision["claim"],
                "observation": decision["observation"],
                "findings": decision["findings"],
            }
            for decision in decisions
        ]

        if not qualified:
            return {
                "qualified": False,
                "outcome": "not_qualifying",
                "reason": "no_indicator_qualified",
                "detail": (
                    "No declared indicator was satisfied by this interaction. Nothing was "
                    "emitted, and nothing was stored: a buyer action that matches no indicator "
                    "is not a signal, and the research requires a signal to have at least one."
                ),
                "indicators": reported,
                "signal": None,
            }

        if "indicators" in fields:
            raise SignalError(
                "an interaction carries observations, not indicators. The indicator is derived "
                "from the observations; naming one here would bypass the match this route "
                "exists to make."
            )

        emission = {
            key: value
            for key, value in payload.items()
            if str(key) not in emission_module.OBSERVATION_FIELDS
        }
        emission["indicators"] = [
            {
                "key": decision["key"],
                "metadata": _indicator_metadata(by_key, decision["key"], observations),
            }
            for decision in qualified
        ]
        result = self.emit(emission, room_id=room_id, actor=actor, source=source)
        return {
            "qualified": True,
            "outcome": result["outcome"],
            "reason": result["reason"],
            "detail": result.get("detail"),
            # Carried through so a sender that retried an interaction can see
            # the same count the emit route reports. Without it, a dropped
            # interaction looks identical to a dropped signal the caller never
            # retried.
            "duplicate_attempts": result.get("duplicate_attempts", 0),
            "indicators": reported,
            "signal": result["signal"],
        }

    def _room(self, room_id: str | None) -> dict[str, Any] | None:
        if not room_id:
            return None
        return self.store.get(room_id)

    def present_signal(self, record: Mapping[str, Any], locale: str | None = None) -> dict[str, Any]:
        """A signal with its rendered sentence, for a single read.

        The stored ``data`` and the stored indicator array travel alongside the
        rendered view rather than being replaced by it, because a caller reading
        one signal usually wants both: the sentence a seller sees and the evidence
        underneath it. A signal whose registration has been withdrawn still reads,
        and says in its warnings that it cannot be rendered.
        """
        data = dict(record.get("data") or {})
        registration = self.registration(str(data.get("registration_id")))
        row = feed_module.feed_entry(
            {
                **data,
                # The id and the room live in the envelope, not in `data`, so they
                # have to be handed over explicitly. A row that came back with a
                # null id would be readable and unaddressable, which is the worst
                # of both.
                "id": record.get("id"),
                "room_id": record.get("room_id"),
            },
            self.present(registration) if registration is not None else None,
            locale,
        )
        return {
            **row,
            "data": data.get("data", {}),
            "indicators": data.get("indicators", []),
            "created_at": record.get("created_at"),
            "revision": record.get("revision"),
        }

    def signal(self, signal_id: str, *, locale: str | None = None) -> dict[str, Any] | None:
        record = self.store.get(signal_id)
        if record is None or record.get("collection") != SIGNALS:
            return None
        return self.present_signal(record, locale)

    def signals(
        self,
        *,
        room_id: str | None,
        type_name: str | None = None,
        urgency: str | None = None,
        broadcast: bool | None = None,
        seller: str | None = None,
        locale: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Signals, newest first.

        Each filter is a JSON path in the signal's own payload and is resolved by
        ``find()`` through the dynamic index - including ``receiver.seller``, which
        is a dotted path into a nested object. That is the schema-flexibility rule
        working: a field this code never declared is queryable the moment it is
        written, with no migration and no change to this method.

        Room scope is a post-filter rather than a ``where`` clause, because
        ``find()`` takes no room and ``room_id`` is a column rather than a JSON
        path. It is a filter, not an access rule: this product's rooms carry no ACL
        and pretending otherwise in a query would be a claim the store cannot back.
        """
        where: dict[str, Any] = {}
        if type_name:
            where["type"] = type_name
        if urgency:
            where["urgency"] = require_urgency(urgency)
        if broadcast is not None:
            where["broadcast_notification"] = broadcast
        if seller:
            where["receiver.seller"] = seller

        if where:
            found = self.store.find(SIGNALS, where, limit=1000)
            records = [
                record for record in found if room_id is None or record.get("room_id") == room_id
            ]
        elif room_id:
            records = self.store.list(SIGNALS, room_id=room_id, limit=1000)
        else:
            records = self.store.list(SIGNALS, limit=1000)

        capped = max(1, min(int(limit), 1000))
        return [self.present_signal(record, locale) for record in records[:capped]]

    def live_feed(
        self, *, room_id: str | None, seller: str | None = None, locale: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """The seller's Live Feed for one room."""
        records = (
            self.store.list(SIGNALS, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(SIGNALS, limit=1000)
        )
        registrations = {
            record["id"]: self.present(record)
            for record in self.store.list(REGISTRATIONS, limit=1000, include_deleted=True)
        }
        signals = [dict(record.get("data") or {}, id=record["id"], room_id=record.get("room_id")) for record in records]
        rows = feed_module.build(
            signals,
            registrations,
            seller=seller,
            locale=locale,
        )[: max(1, min(int(limit), 1000))]
        return {
            "room_id": room_id,
            "seller": seller,
            "count": len(rows),
            "urgent": sum(1 for row in rows if row["urgency"] == "high"),
            "by_urgency": {name: sum(1 for row in rows if row["urgency"] == name) for name in URGENCIES},
            "actionable": 0,
            "actionability_note": ACTIONABILITY_NOTE,
            "entries": rows,
        }

    def summary(self, *, room_id: str | None) -> dict[str, Any]:
        """Counts for the page header, and the actionability note beside them.

        Counted over the room's live signals rather than the whole collection, so
        a room's header says what happened in that room.
        """
        records = (
            self.store.list(SIGNALS, room_id=room_id, limit=1000)
            if room_id
            else self.store.list(SIGNALS, limit=1000)
        )
        signals = [record.get("data") or {} for record in records]
        by_urgency = {name: 0 for name in URGENCIES}
        for data in signals:
            by_urgency[str(data.get("urgency"))] = by_urgency.get(str(data.get("urgency")), 0) + 1
        types: dict[str, int] = {}
        sellers: dict[str, int] = {}
        for data in signals:
            types[str(data.get("type"))] = types.get(str(data.get("type")), 0) + 1
            seller = str((data.get("receiver") or {}).get("seller") or "unresolved")
            sellers[seller] = sellers.get(seller, 0) + 1
        broadcast = sum(1 for data in signals if data.get("broadcast_notification") is True)
        return {
            "room_id": room_id,
            "signals": len(signals),
            "by_urgency": by_urgency,
            "broadcast": broadcast,
            "withheld": len(signals) - broadcast,
            "live_feed": broadcast,
            "duplicates_dropped": sum(int(data.get("duplicate_attempts") or 0) for data in signals),
            "registrations": len(self.store.list(REGISTRATIONS, limit=1000)),
            "by_type": [
                {"type": name, "count": count} for name, count in sorted(types.items())
            ],
            "by_seller": [
                {"seller": name, "count": count} for name, count in sorted(sellers.items())
            ],
            "actionable": 0,
            "actionability_note": ACTIONABILITY_NOTE,
            "attribution_types": list(ATTRIBUTION_TYPES),
        }


def _indicator_metadata(
    declared: Mapping[str, Mapping[str, Any]], key: str, observations: Mapping[str, Any]
) -> dict[str, Any]:
    """The subset of an interaction's observations that belongs to one indicator.

    Only the fields the indicator's own ``metadata_shape`` declares are carried,
    so a signal's indicator metadata is the evidence for that indicator and
    nothing else. An observation the shape does not declare is a fact about the
    interaction, not about this claim.
    """
    shape = (declared.get(key) or {}).get("metadata_shape") or {}
    properties = shape.get("properties")
    if not isinstance(properties, Mapping):
        return {str(k): v for k, v in observations.items()}
    return {
        name: observations[name] for name in properties if name in observations
    }
