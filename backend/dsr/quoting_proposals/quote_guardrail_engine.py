"""WF-090: the reads and the writes for quote guardrails.

Pricing, quoting and proposals. This is the module that talks to the store. The
grammar and the evaluator are in
:mod:`dsr.quoting_proposals.quote_guardrail_rules`, the words in
:mod:`dsr.quoting_proposals.quote_guardrail_vocabulary`, and the judgement calls in
:mod:`dsr.quoting_proposals.quote_guardrail_inferences`. Nothing here decides a rule,
so a change to the grammar does not touch this file.

Nothing in this package imports ``dsr.api``, and nothing opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the
audit row is written in the same transaction as the change.

What this workflow reads and does not own
-----------------------------------------

**The quote and its line items.** WF-086 provisions ``wf086_quote`` and
``wf086_line_item``. This workflow reads them as data and never creates one: a quote
has one writer, and the guardrail's question is whether the written quote may reach the
buyer. The related scopes (deal, company, recipient_contact, current_user) are read
from the quote's own payload, which is where WF-086's open-JSON shape puts them. See
``DERIVED_RELATED_RECORDS_RIDE_ON_THE_QUOTE``.

**Publishing.** WF-094 owns the publish. :meth:`QuoteGuardrailEngine.publish` is the
gate a publisher calls: it answers allowed, or it refuses with the blocking rule's
message. It does not render a quote and it does not create a link.

The shape of the engine
-----------------------

Built per request from ``StoreDep`` rather than held on ``app.state``, because an
``app.state`` entry is exactly the edit to the shared ``dsr/api.py`` that the feature
host exists to make unnecessary. It holds nothing but the store handle and a clock,
both constructor arguments, so a test constructs one over rows of its own with a clock
it controls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from dsr.db.audited import RecordNotFound
from dsr.quoting_proposals import (
    quote_guardrail_rules as rules,
    quote_guardrail_vocabulary as vocab,
)
from dsr.store import RecordStore


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The record's own fields, whether a raw row or a hydrated record arrives."""
    data = record.get("data")
    return data if isinstance(data, Mapping) else record


def _is_many(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


class QuoteGuardrailEngine:
    """The workflow, over one audited store."""

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # ------------------------------------------------------------------ #
    # Rules
    # ------------------------------------------------------------------ #

    def _validated(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Check a rule's fields and return what should be stored.

        Every field is checked before any is written, and every problem is collected,
        so the editor can put each message beside the input that caused it. The
        definition is parsed here, which is the only place a malformed rule can be
        refused.
        """
        name = str(payload.get(vocab.FIELD_NAME) or "").strip()
        definition = str(payload.get(vocab.FIELD_DEFINITION) or "").strip()
        outcome = str(payload.get(vocab.FIELD_OUTCOME) or "").strip()
        message = str(payload.get(vocab.FIELD_MESSAGE) or "").strip()
        status = str(payload.get(vocab.FIELD_STATUS) or "").strip()
        if not status:
            status = (
                vocab.STATUS_ENABLED
                if payload.get(vocab.FIELD_ENABLED, True)
                else vocab.STATUS_DISABLED
            )
        errors: list[dict[str, str]] = []
        if not name:
            name = definition
        if not definition:
            errors.append(
                {"field": vocab.FIELD_DEFINITION, "message": "A rule definition is required."}
            )
        if outcome not in vocab.OUTCOMES:
            errors.append(
                {
                    "field": vocab.FIELD_OUTCOME,
                    "message": (
                        f"Outcome must be one of {', '.join(vocab.OUTCOMES)}; "
                        f"{outcome or 'nothing'} is not a Rule outcome type."
                    ),
                }
            )
        if not message:
            errors.append(
                {
                    "field": vocab.FIELD_MESSAGE,
                    "message": "A message to the quote creator is required.",
                }
            )
        if status not in vocab.STATUSES:
            errors.append(
                {
                    "field": vocab.FIELD_STATUS,
                    "message": f"Status must be one of {', '.join(vocab.STATUSES)}.",
                }
            )
        parsed: dict[str, Any] | None = None
        if definition:
            try:
                parsed = rules.parse(definition)
            except rules.GuardrailRefusal as exc:
                errors.append(
                    {"field": vocab.FIELD_DEFINITION, "message": exc.detail, "error": exc.code}
                )
        if errors:
            raise rules.InvalidRule(
                "The rule could not be saved: " + "; ".join(one["message"] for one in errors),
                errors=errors,
            )
        assert parsed is not None  # definition non-empty and it parsed
        return {
            vocab.FIELD_NAME: name,
            vocab.FIELD_DEFINITION: definition,
            vocab.FIELD_NORMALISED: parsed["normalised"],
            vocab.FIELD_OUTCOME: outcome,
            vocab.FIELD_MESSAGE: message,
            vocab.FIELD_STATUS: status,
            vocab.FIELD_ENABLED: status == vocab.STATUS_ENABLED,
        }

    def create_rule(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Save one rule. The definition is text, parsed once here and stored whole."""
        data = self._validated(payload)
        return self.store.create(
            vocab.RULE_COLLECTION, data, actor=actor, source=source, room_id=room_id
        )

    def list_rules(
        self,
        *,
        room_id: str | None = None,
        status: str | None = None,
        enabled: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Every rule, oldest first, optionally filtered.

        Oldest first because the Manage tab shows rules in the order they were added,
        and a rule list that reorders itself between refreshes is a list a user cannot
        put a finger on.
        """
        listed = self.store.list(
            vocab.RULE_COLLECTION,
            room_id=room_id,
            limit=limit,
            order_by="created_at",
            descending=False,
        )
        if status is not None:
            listed = [one for one in listed if _payload(one).get(vocab.FIELD_STATUS) == status]
        if enabled is not None:
            listed = [one for one in listed if rules.is_enabled(one) is enabled]
        return listed

    def rule(self, rule_id: str) -> dict[str, Any]:
        record = self.store.get(rule_id)
        if record is None or record.get("collection") != vocab.RULE_COLLECTION:
            raise rules.RuleNotFound(f"No quote rule {rule_id!r} exists.", record_id=rule_id)
        return record

    def patch_rule(
        self,
        rule_id: str,
        patch: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Edit a rule, or turn its Status switch on or off.

        The full rule is revalidated after the merge, so a patch that would leave an
        unreadable definition or an unknown outcome is refused rather than stored.
        """
        current = self.rule(rule_id)
        body = _payload(current)
        merged = {
            vocab.FIELD_NAME: body.get(vocab.FIELD_NAME),
            vocab.FIELD_DEFINITION: body.get(vocab.FIELD_DEFINITION),
            vocab.FIELD_OUTCOME: body.get(vocab.FIELD_OUTCOME),
            vocab.FIELD_MESSAGE: body.get(vocab.FIELD_MESSAGE),
            vocab.FIELD_STATUS: body.get(vocab.FIELD_STATUS),
        }
        allowed = {
            vocab.FIELD_NAME,
            vocab.FIELD_DEFINITION,
            vocab.FIELD_OUTCOME,
            vocab.FIELD_MESSAGE,
            vocab.FIELD_STATUS,
            vocab.FIELD_ENABLED,
        }
        for key, value in patch.items():
            if key in allowed:
                merged[key] = value
        if vocab.FIELD_ENABLED in patch and vocab.FIELD_STATUS not in patch:
            merged[vocab.FIELD_STATUS] = (
                vocab.STATUS_ENABLED if patch[vocab.FIELD_ENABLED] else vocab.STATUS_DISABLED
            )
        data = self._validated(merged)
        return self.store.update(rule_id, data, actor=actor, source=source)

    def delete_rule(self, rule_id: str, *, actor: str | None = None, source: str) -> dict[str, Any]:
        """Remove a rule. The evaluations it already produced are left alone."""
        self.rule(rule_id)
        return self.store.delete(rule_id, actor=actor, source=source)

    def validate(self, definition: str) -> dict[str, Any]:
        """The dry run behind the editor: parse a definition and report the outcome.

        Never raises. A rule that cannot be read answers with ``valid: false`` and the
        refusal's own code and sentence, so the editor shows the error beside the
        definition instead of turning a typo into a failed request.
        """
        try:
            parsed = rules.parse(definition)
        except rules.GuardrailRefusal as exc:
            return {
                "valid": False,
                "definition": definition,
                "error": exc.code,
                "detail": exc.detail,
                "reason": vocab.REASON_TEXTS.get(exc.code, exc.detail),
            }
        return {
            "valid": True,
            "definition": definition,
            "normalised": parsed["normalised"],
            "kind": parsed["kind"],
        }

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record.get("collection") != vocab.SOURCE_QUOTES:
            raise RecordNotFound(f"No quote {quote_id!r} exists.")
        return record

    def line_items(self, quote_id: str) -> list[dict[str, Any]]:
        return self.store.find(vocab.LINE_ITEMS, {"quote_id": quote_id}, limit=1000)

    def context_for(self, quote_id: str) -> dict[str, Any]:
        """Assemble the evaluation context the grammar addresses.

        The quote and its line items are collections WF-086 writes; the related scopes
        ride on the quote's payload. Every scope is present even when empty, so a rule
        naming an absent one reports unverifiable rather than raising.
        """
        quote = self.quote(quote_id)
        data = dict(_payload(quote))
        context: dict[str, Any] = {"quote": data}
        for scope in (vocab.SCOPE_DEAL, vocab.SCOPE_COMPANY):
            value = data.get(scope)
            context[scope] = dict(value) if isinstance(value, Mapping) else {}
        for scope in (vocab.SCOPE_RECIPIENT_CONTACT, vocab.SCOPE_CURRENT_USER):
            value = data.get(scope)
            if isinstance(value, Mapping):
                context[scope] = [dict(value)]
            elif _is_many(value):
                context[scope] = [dict(one) for one in value if isinstance(one, Mapping)]
            else:
                context[scope] = []
        context[vocab.SCOPE_LINE_ITEM] = [dict(_payload(one)) for one in self.line_items(quote_id)]
        return context

    def rules_for(self, room_id: str | None) -> list[dict[str, Any]]:
        """Every rule in scope for a room, oldest first.

        A rule with no room is global and applies everywhere; a rule with a room
        applies only there. The envelope carries the room, not the payload, which is
        why the filter reads the record rather than its data.
        """
        listed = self.store.list(
            vocab.RULE_COLLECTION, limit=1000, order_by="created_at", descending=False
        )
        return [one for one in listed if one.get("room_id") in (None, room_id)]

    def evaluate_quote(self, quote_id: str, *, room_id: str | None = None) -> dict[str, Any]:
        """Evaluate every in-scope rule against a quote and split the verdicts.

        Writes nothing: this is what a page polls while a seller builds the quote,
        and "rules evaluate continuously while the quote is edited" must not mean
        "writes a row on every keystroke".
        """
        quote = self.quote(quote_id)
        effective_room = room_id or quote.get("room_id")
        records = self.rules_for(effective_room)
        context = self.context_for(quote_id)
        outcome = rules.evaluate_rules(records, context)
        return {
            "quote_id": quote_id,
            "room_id": effective_room,
            "rules_evaluated": len(records),
            "quote": {
                "id": quote.get("id"),
                "name": _payload(quote).get(vocab.FIELD_NAME),
                "status": _payload(quote).get("hs_status"),
            },
            **outcome,
        }

    def record_evaluation(
        self,
        quote_id: str,
        *,
        actor: str | None = None,
        source: str,
        room_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Evaluate and write one evaluation row, in the transaction the caller names."""
        outcome = self.evaluate_quote(quote_id, room_id=room_id)
        record = self.store.create(
            vocab.EVALUATION_COLLECTION,
            {
                "quote_id": quote_id,
                "blocked": outcome["blocked"],
                "publishable": outcome["publishable"],
                "reason_code": outcome["reason_code"],
                "violation_count": len(outcome["violations"]),
                "warning_count": len(outcome["warnings"]),
                "unverifiable_count": len(outcome["unverifiable"]),
                "blocking_rule_ids": [one["rule_id"] for one in outcome["blocking"]],
                "evaluated_at": self._now().isoformat(),
            },
            actor=actor,
            source=source,
            room_id=outcome["room_id"],
        )
        return outcome, record

    def publish(
        self, quote_id: str, *, actor: str | None = None, source: str, room_id: str | None = None
    ) -> dict[str, Any]:
        """The gate the specification's hard stop describes.

        Every enabled Block publish rule is evaluated. A violation refuses with 409 and
        the blocking rule's own message; a clean quote is allowed and the warnings are
        returned beside it. Both answers are recorded as an attempt and an evaluation,
        so "the product stopped this publish" is a row a reviewer can find.
        """
        outcome = self.evaluate_quote(quote_id, room_id=room_id)
        allowed = not outcome["blocked"]
        message = rules.refusal_message(outcome["blocking"]) if not allowed else ""
        attempt = self.store.create(
            vocab.PUBLISH_ATTEMPT_COLLECTION,
            {
                "quote_id": quote_id,
                "allowed": allowed,
                "reason_code": outcome["reason_code"],
                "blocking_rule_ids": [one["rule_id"] for one in outcome["blocking"]],
                "message": message,
                "attempted_at": self._now().isoformat(),
            },
            actor=actor,
            source=source,
            room_id=outcome["room_id"],
        )
        self.record_evaluation(quote_id, actor=actor, source=source, room_id=outcome["room_id"])
        if not allowed:
            raise rules.PublishBlocked(message, violations=outcome["blocking"])
        return {
            "quote_id": quote_id,
            "allowed": True,
            "reason_code": outcome["reason_code"],
            "attempt": attempt,
            "warnings": outcome["warnings"],
            "unverifiable": outcome["unverifiable"],
        }

    def evaluations(self, quote_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.find(vocab.EVALUATION_COLLECTION, {"quote_id": quote_id}, limit=limit)

    def list_evaluations(
        self, *, room_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        return self.store.list(vocab.EVALUATION_COLLECTION, room_id=room_id, limit=limit)

    def attempts(self, *, quote_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        listed = self.store.list(vocab.PUBLISH_ATTEMPT_COLLECTION, limit=limit)
        if quote_id is not None:
            listed = [one for one in listed if _payload(one).get("quote_id") == quote_id]
        return listed

    def summary(self, *, room_id: str | None = None) -> dict[str, Any]:
        """Counts a reviewer acts on: which rules are on, and what the gate has done."""
        listed = self.list_rules(room_id=room_id, limit=1000)
        enabled = [one for one in listed if rules.is_enabled(one)]
        blocking = [
            one for one in enabled if _payload(one).get(vocab.FIELD_OUTCOME) == vocab.OUTCOME_BLOCK
        ]
        warning = [
            one
            for one in enabled
            if _payload(one).get(vocab.FIELD_OUTCOME) == vocab.OUTCOME_WARNING
        ]
        attempts = self.attempts(limit=1000)
        evaluations = self.list_evaluations(room_id=room_id, limit=1000)
        return {
            "room_id": room_id,
            "rules": {
                "total": len(listed),
                "enabled": len(enabled),
                "disabled": len(listed) - len(enabled),
                "block_publish": len(blocking),
                "show_warning": len(warning),
            },
            "evaluations": {
                "total": len(evaluations),
                "blocked": len([one for one in evaluations if _payload(one).get("blocked")]),
                "publishable": len(
                    [one for one in evaluations if _payload(one).get("publishable")]
                ),
            },
            "publish_attempts": {
                "total": len(attempts),
                "allowed": len([one for one in attempts if _payload(one).get("allowed")]),
                "blocked": len([one for one in attempts if not _payload(one).get("allowed")]),
            },
        }
