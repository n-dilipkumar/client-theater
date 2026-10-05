"""WF-092: the reads and the writes, behind one façade.

:class:`ApprovalChainEngine` is what the HTTP layer calls. It holds nothing but a
:class:`~dsr.store.RecordStore` handle, which is why the feature module builds one per
request from ``StoreDep`` rather than hanging it on ``app.state`` - an ``app.state``
entry would mean editing ``dsr/api.py``, and the whole point of the feature host is
that adding a workflow is adding a file.

Every method that writes takes a required ``source=``. That is not decoration. The
audit row is the product's guarantee, and an audit row that names a string rather than
a route cannot be traced back to the request that caused it. A required keyword means
the omission is a ``TypeError`` at the call site rather than a silently untraceable row
in production.

Six collections, all schema-flexible
-------------------------------------
``wf092_quote_approval_workflow``, ``wf092_approval_branch``, ``wf092_approval_step``,
``wf092_approval_enrolment``, ``wf092_approval_decision`` and
``wf092_approval_notification``. None has a migration, a typed column, or a required
field beyond the researched contract, because a team adding a field must not need to
coordinate with anyone. Every filter goes through ``find()`` and therefore through the
dynamic index, so a field added later is queryable the moment it is written.

The chain a quote walks
-----------------------
:meth:`enrol` is the whole workflow in one call, and the order matters:

1. read the single workflow, and refuse to create a second one;
2. evaluate every branch against the quote;
3. push an approval step for each qualifying branch;
4. if no step was pushed, auto-approve - the researched valve;
5. otherwise enrol at priority 1 and notify the approvers at that priority only.

:meth:`decide` is the other half. It records one approver's decision, re-reads the
chain, and writes the final state only when the last decision lands. The next priority
is recomputed every time from the stored decisions rather than kept as a counter, so a
chain cannot drift out of step with its own history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from dsr.quoting_proposals import approval_chain_rules as rules, approval_chain_vocabulary as vocab

#: The six collections this workflow owns, named here so a filter and a route cannot
#: disagree about which one they mean.
WORKFLOW = vocab.WORKFLOW
BRANCHES = vocab.BRANCHES
APPROVAL_STEPS = vocab.APPROVAL_STEPS
ENROLMENTS = vocab.ENROLMENTS
DECISIONS = vocab.DECISIONS
NOTIFICATIONS = vocab.NOTIFICATIONS
SOURCE_QUOTES = vocab.SOURCE_QUOTES


class WorkflowNotFound(LookupError):
    """No approval workflow exists yet."""


class DuplicateWorkflow(LookupError):
    """A second approval workflow was requested.

    "You can't duplicate the workflow or create a new workflow to use for quote
    approvals." The engine raises rather than storing two and letting a caller pick.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _payload(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Flatten a stored record into its payload, keeping the record id.

    Every record this project stores nests its fields under ``data``, so a read that
    skips this returns an envelope and every field lookup silently misses. The id is
    merged back in because the payload has no column of its own for it, and a caller
    needs to address the record it just read.
    """
    if not record:
        return {}
    return {**dict(record.get("data") or {}), "id": record.get("id")}


def _payloads(rows: Any) -> list[dict[str, Any]]:
    """Flatten every record in a read."""
    return [_payload(row) for row in rows]


class ApprovalChainEngine:
    """Branch, enrol, and run the sequential approval chain."""

    def __init__(self, store: Any, *, now: Any = None) -> None:
        self.store = store
        self._now = now or _now

    # ----------------------------------------------------------------- #
    # The single workflow
    # ----------------------------------------------------------------- #

    def ensure_workflow(
        self,
        *,
        source: str,
        name: str = "Sequential quote approval",
        re_enroll: bool = False,
        workflow_id: str = vocab.SINGLE_WORKFLOW_ID,
    ) -> dict[str, Any]:
        """Return the one approval workflow, creating it if it is absent.

        A caller that names a different id is refused. The research is explicit that the
        approval workflow is single and cannot be duplicated, so the id is fixed here
        rather than trusted from a payload.
        """
        if workflow_id != vocab.SINGLE_WORKFLOW_ID:
            raise DuplicateWorkflow(
                "the quote approval workflow is single, so a second workflow cannot be created"
            )
        existing = self.store.find(WORKFLOW, {"workflow_id": vocab.SINGLE_WORKFLOW_ID})
        if existing:
            return _payload(existing[0])
        if self.store.count_where(WORKFLOW, {}) > 0:
            raise DuplicateWorkflow(
                "the quote approval workflow is single, so a second workflow cannot be created"
            )
        return _payload(
            self.store.create(
                WORKFLOW,
                {
                    "workflow_id": vocab.SINGLE_WORKFLOW_ID,
                    "name": name,
                    vocab.RE_ENROL_KEY: bool(re_enroll),
                    "created_at": self._now(),
                },
                actor="wf-092",
                source=source,
            )
        )

    def get_workflow(self) -> dict[str, Any]:
        """The one approval workflow, or a refusal when none exists."""
        found = self.store.find(WORKFLOW, {"workflow_id": vocab.SINGLE_WORKFLOW_ID})
        if not found:
            raise WorkflowNotFound("no quote approval workflow exists yet")
        return _payload(found[0])

    # ----------------------------------------------------------------- #
    # Branches
    # ----------------------------------------------------------------- #

    def add_branch(
        self,
        *,
        source: str,
        name: str,
        prop: str = vocab.PROPERTY_QUOTE_AMOUNT,
        operator: str = "greater_than",
        threshold: float = vocab.DEFAULT_BRANCH_THRESHOLD,
        sequences: Sequence[Mapping[str, Any]] = (),
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Add one branch and the approval steps it pushes.

        ``sequences`` is the ranked approver list the branch pushes onto a qualifying
        quote: one entry per priority, each with ``priority``, ``approvers`` and an
        optional ``requirement``. Both researched caps are enforced here, before any row
        is written.
        """
        self.ensure_workflow(source=source)
        rules.validate_sequence(list(sequences), existing_sequences=0)
        branch = _payload(
            self.store.create(
                BRANCHES,
                {
                    "name": name,
                    "property": prop,
                    "operator": operator,
                    "threshold": threshold,
                    "sequences": [dict(sequence) for sequence in sequences],
                    "created_at": self._now(),
                },
                room_id=room_id,
                actor="wf-092",
                source=source,
            )
        )
        return branch

    def list_branches(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every configured branch, oldest first."""
        return _payloads(
            self.store.list(BRANCHES, limit=limit, order_by="created_at", descending=False)
        )

    def list_branches_with_steps(self) -> list[dict[str, Any]]:
        """Each branch with the approval step rows it would push.

        The panel needs the steps next to the branch that qualifies them. The steps are
        derived rather than stored, because the researched action pushes a step *onto
        the quote* at enrolment time and not at configuration time.
        """
        result = []
        for branch in self.list_branches():
            steps: list[dict[str, Any]] = []
            for sequence in branch.get("sequences") or []:
                for approver in sequence.get("approvers") or []:
                    steps.append(
                        {
                            vocab.PRIORITY_KEY: sequence.get(vocab.PRIORITY_KEY),
                            vocab.APPROVER_KEY: approver,
                            vocab.MESSAGE_KEY: sequence.get(vocab.MESSAGE_KEY),
                            "requirement": sequence.get("requirement")
                            or vocab.REQUIREMENT_SEQUENTIAL,
                        }
                    )
            result.append({**branch, "steps": rules.group_by_priority(steps)})
        return result

    # ----------------------------------------------------------------- #
    # Quotes
    # ----------------------------------------------------------------- #

    def find_quote(self, quote_id: str) -> dict[str, Any] | None:
        """One authored quote, read as data.

        WF-086 provisions these rows. This workflow never creates one.
        """
        return _payload(self.store.get(quote_id)) or None

    def list_quotes(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """The quotes a branch can be evaluated against."""
        return _payloads(
            self.store.list(SOURCE_QUOTES, limit=limit, order_by="created_at", descending=False)
        )

    # ----------------------------------------------------------------- #
    # Enrolment
    # ----------------------------------------------------------------- #

    def evaluate(self, quote: Mapping[str, Any]) -> dict[str, Any]:
        """Run every configured branch against one quote.

        Nothing is written. The panel calls this to show which branches qualify before
        anybody enrols, which is what the workflow editor's **None met** path shows.
        """
        branches = self.list_branches()
        results = [rules.evaluate_branch(quote, branch) for branch in branches]
        qualifying = [
            {"branch": branch, "verdict": result}
            for branch, result in zip(branches, results, strict=True)
            if result["outcome"] == vocab.OUTCOME_QUALIFIED
        ]
        return {
            "quote_id": quote.get("id"),
            "branches": branches,
            "qualified": bool(qualifying),
            "qualifying_branches": qualifying,
            "results": results,
        }

    def enrol(
        self,
        quote: Mapping[str, Any] | str,
        *,
        source: str,
        actor: str = "wf-092",
    ) -> dict[str, Any]:
        """Branch on a quote, push its approval steps, and start the chain.

        This is the researched sequence in one call: evaluate the branches, push an
        approval step for each qualifying branch, then start the approval flow. When no
        step qualified, the researched valve applies and the quote is auto-approved.
        """
        workflow = self.ensure_workflow(source=source)
        if isinstance(quote, str):
            found = self.find_quote(quote)
            if found is None:
                raise LookupError(f"quote {quote} does not exist")
            quote = found
        else:
            quote = _payload(quote)

        evaluation = self.evaluate(quote)
        steps: list[dict[str, Any]] = []
        for qualifying in evaluation["qualifying_branches"]:
            steps.extend(_steps_for_branch(qualifying["branch"], qualifying["verdict"]))

        levels = rules.group_by_priority(steps)

        if not levels:
            result = rules.auto_approve(str(quote.get("id")), evaluation["results"])
            enrolment = _payload(
                self.store.create(
                    ENROLMENTS,
                    {
                        "quote_id": quote.get("id"),
                        "state": result["state"],
                        vocab.QUOTE_STATUS_KEY: result[vocab.QUOTE_STATUS_KEY],
                        "publishable": result["publishable"],
                        "auto_approved": True,
                        "reason": result["reason"],
                        "decisions": {},
                        "run": 1,
                        "created_at": self._now(),
                    },
                    actor=actor,
                    source=source,
                )
            )
            return {
                "enrolment": enrolment,
                "evaluation": evaluation,
                "auto_approved": True,
                "state": result["state"],
                "publishable": True,
                "reason": result["reason"],
                "levels": [],
            }

        first = levels[0]
        rendered = [self._render(step.get(vocab.MESSAGE_KEY), quote) for step in steps]
        enrolment = _payload(
            self.store.create(
                ENROLMENTS,
                {
                    "quote_id": quote.get("id"),
                    "state": vocab.STATE_PENDING,
                    vocab.QUOTE_STATUS_KEY: None,
                    "publishable": False,
                    "auto_approved": False,
                    "workflow_id": workflow.get("workflow_id"),
                    "active_priority": first[vocab.PRIORITY_KEY],
                    "decisions": {},
                    "run": 1,
                    "created_at": self._now(),
                },
                actor=actor,
                source=source,
            )
        )

        for step, message in zip(steps, rendered, strict=True):
            self.store.create(
                APPROVAL_STEPS,
                {
                    "enrolment_id": enrolment["id"],
                    "quote_id": quote.get("id"),
                    vocab.PRIORITY_KEY: step[vocab.PRIORITY_KEY],
                    vocab.APPROVER_KEY: step[vocab.APPROVER_KEY],
                    "requirement": step["requirement"],
                    "message": message["text"],
                    "created_at": self._now(),
                },
                actor=actor,
                source=source,
            )

        self._notify(
            enrolment, steps, levels[0][vocab.PRIORITY_KEY], quote, source=source, actor=actor
        )

        return {
            "enrolment": enrolment,
            "evaluation": evaluation,
            "auto_approved": False,
            "state": enrolment["state"],
            "publishable": False,
            "reason": (
                "A qualifying branch pushed an approval step, so the chain waits on the "
                "approvers at the first priority."
            ),
            "levels": levels,
            "messages": rendered,
        }

    def _render(self, template: Any, quote: Mapping[str, Any]) -> dict[str, Any]:
        return rules.render_message(template or "Please review this quote.", quote)

    def _notify(
        self,
        enrolment: Mapping[str, Any],
        steps: Sequence[Mapping[str, Any]],
        priority: int,
        quote: Mapping[str, Any],
        *,
        source: str,
        actor: str,
    ) -> list[dict[str, Any]]:
        """Notify the approvers at the active priority, and nobody else.

        "The sales director won't need to approve the quote until the sales manager has
        completed their approval." Only the two channels this product can deliver are
        written; the researched channels it cannot are recorded in the inferences module.
        """
        active = [step for step in steps if step[vocab.PRIORITY_KEY] == priority]
        rows = []
        for channel in vocab.DELIVERY_CHANNELS_BUILT:
            for step in active:
                message = self._render(step.get(vocab.MESSAGE_KEY), quote)
                rows.append(
                    self.store.create(
                        NOTIFICATIONS,
                        {
                            "enrolment_id": enrolment["id"],
                            "quote_id": enrolment.get("quote_id"),
                            "channel": channel,
                            vocab.APPROVER_KEY: step[vocab.APPROVER_KEY],
                            vocab.PRIORITY_KEY: priority,
                            "message": message["text"],
                            "status": "recorded",
                            "created_at": self._now(),
                        },
                        actor=actor,
                        source=source,
                    )
                )
        return rows

    # ----------------------------------------------------------------- #
    # Decisions
    # ----------------------------------------------------------------- #

    def steps_for(self, enrolment_id: str) -> list[dict[str, Any]]:
        """The approval step rows belonging to one enrolment.

        Sorted by priority and then by approver. The store returns rows in insertion
        order, which is not a contract, so a chain whose two approvers sit at the same
        priority would otherwise notify them in whatever order the database happened to
        answer. Sorting here is what makes the sequence a sequence.
        """
        rows = _payloads(self.store.find(APPROVAL_STEPS, {"enrolment_id": enrolment_id}))
        return sorted(
            rows,
            key=lambda step: (
                int(step.get(vocab.PRIORITY_KEY) or 0),
                str(step.get(vocab.APPROVER_KEY) or ""),
            ),
        )

    def levels_for(self, enrolment_id: str) -> list[dict[str, Any]]:
        """The stored step rows grouped into priority levels, lowest priority first."""
        return rules.group_by_priority(self.steps_for(enrolment_id))

    def decide(
        self,
        enrolment_id: str,
        approver: str,
        decision: str,
        *,
        source: str,
        actor: str = "wf-092",
    ) -> dict[str, Any]:
        """Record one approver's decision and advance the chain.

        The next priority is recomputed from the stored decisions after the write, so a
        chain cannot disagree with its own history. The final status and the
        publishability flag are written only when the last decision lands, which is what
        "the last decision writes APPROVED/REJECTED" means.
        """
        enrolment = _payload(self.store.require(enrolment_id))
        levels = self.levels_for(enrolment_id)
        outcome = rules.advance(enrolment, levels, approver, decision)

        if outcome["outcome"] in {
            "not_an_approver",
            "not_yet_your_priority",
            "already_decided",
            "auto_approved",
        }:
            return {"enrolment": enrolment, "levels": levels, **outcome}

        row = self.store.create(
            DECISIONS,
            {
                "enrolment_id": enrolment_id,
                "quote_id": enrolment.get("quote_id"),
                vocab.APPROVER_KEY: approver,
                "decision": decision,
                vocab.PRIORITY_KEY: outcome.get("next_priority"),
                "created_at": self._now(),
            },
            actor=actor,
            source=source,
        )

        patch: dict[str, Any] = {"decisions": outcome["decisions"], "state": outcome["state"]}
        if outcome.get("next_priority") is not None:
            patch["active_priority"] = outcome["next_priority"]
        if outcome["outcome"] == "complete":
            patch[vocab.QUOTE_STATUS_KEY] = outcome[vocab.QUOTE_STATUS_KEY]
            patch["publishable"] = outcome["publishable"]
            patch["active_priority"] = None
            patch["decided_at"] = self._now()
        updated = _payload(self.store.update(enrolment_id, patch, actor=actor, source=source))

        next_priority = outcome.get("next_priority")
        if next_priority is not None:
            steps = [
                step
                for step in self.steps_for(enrolment_id)
                if step[vocab.PRIORITY_KEY] == next_priority
            ]
            quote = _payload(self.store.get(str(enrolment.get("quote_id"))))
            self._notify(updated, steps, next_priority, quote, source=source, actor=actor)

        return {"enrolment": updated, "levels": levels, "decision_row": row, **outcome}

    def re_enrol(
        self,
        enrolment_id: str,
        *,
        source: str,
        actor: str = "wf-092",
    ) -> dict[str, Any]:
        """Send a decided chain back to the first priority.

        Only meaningful when the workflow's re-enrol switch is on, which is what the
        research says the toggle is for.
        """
        workflow = self.get_workflow()
        if not workflow.get(vocab.RE_ENROL_KEY):
            raise rules.ApprovalRuleError(
                "re-enrolment is off for this workflow, so an edited quote is not sent back"
            )
        enrolment = _payload(self.store.require(enrolment_id))
        reset = rules.re_enrol(enrolment)
        updated = _payload(
            self.store.update(
                enrolment_id,
                {
                    "state": reset["state"],
                    "active_priority": reset["active_priority"],
                    "decisions": reset["decisions"],
                    "run": reset["run"],
                    vocab.QUOTE_STATUS_KEY: None,
                    "publishable": False,
                    "re_enrolled_at": self._now(),
                },
                actor=actor,
                source=source,
            )
        )
        return {"enrolment": updated, "previous_run": reset["previous_run"], "run": reset["run"]}

    # ----------------------------------------------------------------- #
    # Reads
    # ----------------------------------------------------------------- #

    def list_enrolments(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every enrolment, newest first, each with its levels."""
        rows = _payloads(
            self.store.list(ENROLMENTS, limit=limit, order_by="created_at", descending=True)
        )
        for row in rows:
            row["levels"] = self.levels_for(row["id"])
        return rows

    def get_enrolment(self, enrolment_id: str) -> dict[str, Any]:
        """One enrolment with its levels, decisions and notifications."""
        enrolment = _payload(self.store.require(enrolment_id))
        return {
            **enrolment,
            "levels": self.levels_for(enrolment_id),
            "decision_rows": self.decisions_for(enrolment_id),
            "notifications": self.notifications_for(enrolment_id),
        }

    def notifications_for(self, enrolment_id: str) -> list[dict[str, Any]]:
        """The notifications fired for one enrolment."""
        return _payloads(self.store.find(NOTIFICATIONS, {"enrolment_id": enrolment_id}))

    def decisions_for(self, enrolment_id: str) -> list[dict[str, Any]]:
        """The decisions recorded for one enrolment."""
        return _payloads(self.store.find(DECISIONS, {"enrolment_id": enrolment_id}))


def _steps_for_branch(
    branch: Mapping[str, Any], verdict: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The approval step rows one qualifying branch pushes onto a quote.

    "Add quote approval step" is the researched action, and it runs once per qualifying
    branch, so each approver at each priority in that branch's sequence becomes a step
    row on the enrolment. Reading the sequence off the branch record keeps the push a
    pure function of the configuration, so two branches reading the same property stay
    distinguishable by their id rather than by the field they happen to share.
    """
    steps: list[dict[str, Any]] = []
    for sequence in branch.get("sequences") or []:
        for approver in sequence.get("approvers") or []:
            steps.append(
                {
                    vocab.PRIORITY_KEY: sequence.get(vocab.PRIORITY_KEY),
                    vocab.APPROVER_KEY: approver,
                    vocab.MESSAGE_KEY: sequence.get(vocab.MESSAGE_KEY),
                    "requirement": sequence.get("requirement") or vocab.REQUIREMENT_SEQUENTIAL,
                    "branch_id": branch.get("id"),
                    "branch_property": verdict.get("property"),
                    "threshold": verdict.get("threshold"),
                }
            )
    return steps
