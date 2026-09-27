"""Where a rendered request actually goes.

Two transports, one interface:

:class:`UrllibTransport`
    The real one. Posts the rendered bytes to the connector's base URL. A
    deployment points this at Salesforce, Dataverse or HubSpot and the rest of
    the workflow is unchanged.
:class:`LocalCrm`
    An in-process CRM that executes the plan and writes the rows it creates into
    the audited store. It exists because the researched flow has a step this
    product otherwise could not show at all: *"The CRM executes subrequests in
    order, capturing each created record id"*, and *"On any failure the whole
    bundle rolls back (strict mode) and the room shows a single actionable
    error."* A connector with no CRM behind it can print a request; it cannot
    demonstrate a rollback, and a reviewer has no way to see that the two rows a
    strict bundle created are gone.

The two behaviours the local CRM honours are both sourced:

* **Ordering.** With ``collateSubrequests`` on, same-type subrequests may be
  grouped together, so an implicit dependency is not ordered against it. The
  local CRM groups by type, and a step that ends up before its declared parent
  fails with :data:`~dsr.atomic_bundle.vocabulary.FAIL_COLLATION_VIOLATION` -
  the sourced caveat, reproduced rather than described.
* **Atomicity.** Strict rolls back every row the run created, including the ones
  that already succeeded. Partial keeps what was created and does not execute
  the dependents of a failed subrequest - "Dependent subrequests aren't
  executed."

Where a row goes, it goes through :class:`~dsr.store.RecordStore`, so its audit
row is written in the same transaction as the row. The ``source`` is the route
that asked for the commit, passed in by the caller - never a literal here, which
is the defect this programme has shipped before.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from dsr.atomic_bundle.dialects import RequestDocument, RequestPart
from dsr.atomic_bundle.references import (
    content_id_for,
    resolve_dv,
    resolve_sf,
    sf_references,
)
from dsr.atomic_bundle.vocabulary import (
    DATAVERSE_BATCH,
    FAIL_COLLATION_VIOLATION,
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_ROLLED_BACK,
    OUTCOME_SKIPPED,
    POLICY_STRICT,
    SALESFORCE_COMPOSITE,
    SKIP_DEPENDENCY_FAILED,
)
from dsr.store import RecordStore

#: Where a row the CRM created is stored. A separate collection from the bundle
#: and the run, so "what the CRM now holds" is one query and a rollback is
#: visible as rows leaving it.
TARGET_COLLECTION = "crm_atomic_target"

#: The note attached to a run this build could not fully interpret. Kept as a
#: constant so a deployment reads the same words in the UI and in the tests.
RESPONSE_NOT_PARSED_NOTE = (
    "This build does not parse a vendor composite response body, so the "
    "per-subrequest outcomes are unknown. The raw response is on the run record's "
    "response_body."
)

#: The message a skipped dependent gets, quoting the rule that skips it.
DEPENDENT_SKIPPED_MESSAGE = (
    "[sourced] Dependent subrequests aren't executed: {parent!r} did not succeed, so "
    "there is no record for this one to point at."
)

#: The message a rolled-back row gets, quoting the rule that rolled it back.
ROLLED_BACK_MESSAGE = (
    "[sourced] allOrNone: true - the entire composite request is rolled back, including "
    "the subrequests that already succeeded."
)


@dataclass(frozen=True)
class StepOutcome:
    """What one subrequest did.

    One row per declared step, always - including the ones that never ran. A
    strict rollback produces ``rolled_back`` rows rather than missing ones,
    because "the Account was created and then the bundle rolled back" is the fact
    a rep needs when they ask what happened.
    """

    reference_id: str
    record_type: str
    outcome: str
    depends_on: tuple[str, ...] = ()
    record_id: str = ""
    reason: str = ""
    message: str = ""
    sent: dict[str, Any] = field(default_factory=dict)
    resolved: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "record_type": self.record_type,
            "outcome": self.outcome,
            "depends_on": list(self.depends_on),
            "record_id": self.record_id,
            "reason": self.reason,
            "message": self.message,
            "sent": self.sent,
            "resolved": self.resolved,
        }


@dataclass(frozen=True)
class CommitResult:
    """The answer to "what did that one request do?"."""

    ok: bool
    status: int
    body: str = ""
    error: str = ""
    duration_ms: float = 0.0
    steps: tuple[StepOutcome, ...] = ()
    committed: tuple[str, ...] = ()
    created_record_ids: tuple[str, ...] = ()
    rolled_back: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    actionable_error: dict[str, Any] | None = None
    atomic: bool = True
    compensated: bool = False
    compensation_failures: tuple[str, ...] = ()
    response_body: str = ""
    notes: tuple[str, ...] = ()

    def summary(self) -> dict[str, int]:
        counts = {
            outcome: 0
            for outcome in (
                OUTCOME_CREATED,
                OUTCOME_ROLLED_BACK,
                OUTCOME_SKIPPED,
                OUTCOME_FAILED,
            )
        }
        for step in self.steps:
            counts[step.outcome] = counts.get(step.outcome, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "atomic": self.atomic,
            "compensated": self.compensated,
            "compensation_failures": list(self.compensation_failures),
            "counts": self.summary(),
            "committed": list(self.committed),
            "created_record_ids": list(self.created_record_ids),
            "rolled_back": list(self.rolled_back),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "actionable_error": self.actionable_error,
            "steps": [step.to_dict() for step in self.steps],
            "notes": list(self.notes),
        }


class Transport(Protocol):
    """What a rendered request needs from whatever is on the other end."""

    def send(self, document: RequestDocument, *, source: str) -> CommitResult: ...


# --------------------------------------------------------------------------- #
# The real one
# --------------------------------------------------------------------------- #


@dataclass
class UrllibTransport:
    """Posts the rendered bytes to a real CRM.

    It records the HTTP answer and the response body verbatim, and it does not
    guess at the per-subrequest outcomes: the research does not quote a composite
    response body, so parsing one would be this build inventing a contract. A
    deployment reads the raw body off the run record; the step outcomes are left
    empty rather than confidently wrong, and the run says why.
    """

    base_url: str = ""
    timeout: float = 30.0

    def send(self, document: RequestDocument, *, source: str) -> CommitResult:
        started = time.perf_counter()
        url = f"{self.base_url.rstrip('/')}{document.path}"
        request = urllib.request.Request(
            url,
            data=document.body.encode("utf-8") if document.body else None,
            method=document.method or "POST",
            headers={**document.headers},
        )
        raw = ""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8", "replace")
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            status = int(exc.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return CommitResult(
                ok=False,
                status=0,
                error=f"could not reach {url}: {exc}",
                duration_ms=_elapsed(started),
                atomic=document.atomic,
                notes=(RESPONSE_NOT_PARSED_NOTE,),
            )

        return CommitResult(
            ok=200 <= status < 300,
            status=status,
            body=raw,
            error="" if 200 <= status < 300 else f"HTTP {status}",
            duration_ms=_elapsed(started),
            atomic=document.atomic,
            response_body=raw,
            notes=(RESPONSE_NOT_PARSED_NOTE,),
        )


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


# --------------------------------------------------------------------------- #
# The local one
# --------------------------------------------------------------------------- #


@dataclass
class LocalCrm:
    """An in-process CRM that executes the plan, in the store, under audit.

    Rows land in :data:`TARGET_COLLECTION`, so the state a bundle leaves behind is
    a query rather than a claim, and a strict rollback is visible as rows leaving
    it.

    ``faults`` maps a subrequest's ``referenceId`` - or a sequence part's id, or
    ``<referenceId>-delete`` for a refused compensating delete - to the error the
    CRM refuses it with. Everything else succeeds. That is the only way a failure
    is produced, which is deliberate: a demo failure produced by a heuristic would
    be a demo of the heuristic.
    """

    store: RecordStore
    faults: Mapping[str, str] = field(default_factory=dict)
    run_id: str = ""
    room_id: str = ""
    actor: str = "local-crm"

    def send(self, document: RequestDocument, *, source: str) -> CommitResult:
        started = time.perf_counter()
        plan = document.plan
        outcomes: dict[str, StepOutcome] = {}
        created_rows: dict[str, str] = {}
        notes: list[str] = list(document.notes)

        if document.is_sequence:
            self._run_sequence(document, outcomes, created_rows, source=source)
        else:
            self._run_single(document, outcomes, created_rows, source=source)

        strict = plan.policy == POLICY_STRICT
        atomic = plan.atomic
        compensation_failures: list[str] = []

        # Captured *before* any undo, and kept as a set. A failure that a rollback
        # then undid still happened, and reporting the run as successful because
        # the rows went away would be the one answer nobody can act on - which is
        # exactly why the research asks the room for a single actionable error. The
        # message is captured here too, because after a rollback the step's
        # outcome says "rolled back", and quoting that back at a rep would point
        # them at the rollback rather than at the thing that caused it.
        failed_set = {
            ref for ref, step in outcomes.items() if step.outcome == OUTCOME_FAILED
        }
        failed = [ref for ref in plan.order if ref in failed_set]
        actionable = _actionable(outcomes, plan.order, failed)

        rolled_back: list[str] = []
        if strict and not atomic and failed_set and created_rows:
            # No documented transaction, so strict is honoured by undoing - and
            # only on a failure. Compensating a run that succeeded would delete
            # the rows it had just committed, which is the sort of thing that
            # looks like a rollback until you read the failure count.
            rolled_back, compensation_failures = self._compensate(
                created_rows, outcomes, source=source
            )
        elif strict and atomic and failed_set:
            # allOrNone: true - "the entire composite request is rolled back",
            # including the subrequests that already succeeded. In partial mode
            # nothing is rolled back: the sourced rule is that the remaining
            # *independent* subrequests are executed and the dependents are not,
            # which is what the skip above already did.
            rolled_back = self._rollback(created_rows, outcomes, source=source)

        committed = _with_outcome(plan, outcomes, OUTCOME_CREATED)
        skipped = _with_outcome(plan, outcomes, OUTCOME_SKIPPED)
        rolled_back = [ref for ref in plan.order if ref in set(rolled_back)]

        if compensation_failures:
            notes.append(
                "A compensating delete was refused, so the rows remain: a compensation is "
                "not a rollback."
            )

        return CommitResult(
            ok=not failed,
            status=200 if not failed else 400,
            body=json.dumps({"outcomes": [outcomes[s.reference_id].to_dict() for s in plan.steps]}),
            error="" if not failed else failed[0],
            duration_ms=_elapsed(started),
            steps=tuple(outcomes[step.reference_id] for step in plan.steps),
            committed=tuple(committed),
            created_record_ids=tuple(created_rows[reference] for reference in created_rows),
            rolled_back=tuple(rolled_back),            failed=tuple(failed),
            skipped=tuple(skipped),
            actionable_error=actionable,
            atomic=atomic,
            compensated=bool(rolled_back) and not atomic,
            compensation_failures=tuple(compensation_failures),
            notes=tuple(notes),
        )

    # -- ordering ----------------------------------------------------------- #

    def _execution_order(self, document: RequestDocument) -> list[str]:
        """The order the CRM will really run the subrequests in.

        [sourced] ``collateSubrequests: true`` groups subrequests of the same type
        together. The groups run in first-appearance order, so a type that appears
        late runs late - but a step of a type that already appeared can be pulled
        forward past a step it depends on. That is the caveat the research quotes,
        reproduced rather than described.
        """
        plan = document.plan
        order = [step.reference_id for step in plan.steps]
        if plan.dialect != SALESFORCE_COMPOSITE or not plan.collate_subrequests:
            return order

        by_type: dict[str, list[str]] = {}
        for step in plan.steps:
            by_type.setdefault(step.record_type, []).append(step.reference_id)

        collated: list[str] = []
        for step in plan.steps:
            for reference in by_type[step.record_type]:
                if reference not in collated:
                    collated.append(reference)
        return collated

    # -- single-request dialects ------------------------------------------- #

    def _run_single(
        self,
        document: RequestDocument,
        outcomes: dict[str, StepOutcome],
        created_rows: dict[str, str],
        *,
        source: str,
    ) -> None:
        plan = document.plan
        by_reference = {step.reference_id: step for step in plan.steps}
        position_of = {ref: index for index, ref in enumerate(self._execution_order(document))}

        for reference in position_of:
            step = by_reference[reference]

            late_parent = _parent_after(step, position_of)
            if late_parent is not None:
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=step.record_type,
                    outcome=OUTCOME_FAILED,
                    depends_on=step.dependencies(),
                    reason=FAIL_COLLATION_VIOLATION,
                    message=(
                        f"collateSubrequests grouped {reference!r} with its own type, so it "
                        f"would have run before {late_parent!r}, which it depends on. Set "
                        "collateSubrequests to false to execute in the declared order."
                    ),
                )
                continue

            blocked = _blocked_by(step, outcomes)
            if blocked is not None:
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=step.record_type,
                    outcome=OUTCOME_SKIPPED,
                    depends_on=step.dependencies(),
                    reason=SKIP_DEPENDENCY_FAILED,
                    message=DEPENDENT_SKIPPED_MESSAGE.format(parent=blocked),
                )
                continue

            unmet = _unmet_implicit(step, plan, created_rows)
            if unmet is not None:
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=step.record_type,
                    outcome=OUTCOME_FAILED,
                    depends_on=step.dependencies(),
                    reason=FAIL_COLLATION_VIOLATION,
                    message=(
                        f"collateSubrequests grouped subrequests of the same type together, so "
                        f"this subrequest ran before {unmet!r}, which its trigger reads. The "
                        "dependency is implicit - there is no reference to order against - so "
                        "only the ordering flag can guarantee it. Set collateSubrequests to "
                        "false."
                    ),
                    sent=dict(step.fields),
                )
                continue

            fault = self.faults.get(reference)
            if fault:
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=step.record_type,
                    outcome=OUTCOME_FAILED,
                    depends_on=step.dependencies(),
                    reason="crm_refused",
                    message=fault,
                    sent=dict(step.fields),
                )
                continue

            sent, resolved, record_id = self._write_step(
                document, step, created_rows, source=source
            )
            outcomes[reference] = StepOutcome(
                reference_id=reference,
                record_type=step.record_type,
                outcome=OUTCOME_CREATED,
                depends_on=step.dependencies(),
                record_id=record_id,
                sent=sent,
                resolved=resolved,
            )

    # -- sequence dialects (HubSpot) ---------------------------------------- #

    def _run_sequence(
        self,
        document: RequestDocument,
        outcomes: dict[str, StepOutcome],
        created_rows: dict[str, str],
        *,
        source: str,
    ) -> None:
        """Execute a HubSpot sequence part by part, in the order they render.

        The batch create covers every Contact in one request; the single creates
        and the association ``PUT`` s cover everything else. Each part is a step's
        worth of work, and a part that fails marks the steps it covers - which is
        what makes a sequence honest about its own partial progress.
        """
        plan = document.plan

        for part in document.parts:
            covered = _part_steps(part, plan)
            fault = self.faults.get(part.reference_id)

            for reference in covered:
                step = plan.step(reference)
                if step is None:  # pragma: no cover - a part always names real steps
                    continue

                if fault:
                    outcomes[reference] = StepOutcome(
                        reference_id=reference,
                        record_type=step.record_type,
                        outcome=OUTCOME_FAILED,
                        depends_on=step.dependencies(),
                        reason="crm_refused",
                        message=fault,
                    )
                    continue

                if part.kind == "association":
                    # A link whose parent never arrived is skipped, not failed:
                    # nothing went wrong with the link, its input is missing.
                    if step.parent and step.parent["reference"] not in created_rows:
                        outcomes[reference] = StepOutcome(
                            reference_id=reference,
                            record_type=step.record_type,
                            outcome=OUTCOME_SKIPPED,
                            depends_on=step.dependencies(),
                            reason=SKIP_DEPENDENCY_FAILED,
                            message=DEPENDENT_SKIPPED_MESSAGE.format(
                                parent=step.parent["reference"]
                            ),
                        )
                    continue

                _sent, _resolved, record_id = self._write_step(
                    document, step, created_rows, source=source
                )
                if step.parent:
                    self._link(
                        record_id, created_rows.get(step.parent["reference"], ""), source=source
                    )
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=step.record_type,
                    outcome=OUTCOME_CREATED,
                    depends_on=step.dependencies(),
                    record_id=record_id,
                )

    # -- writing one row ----------------------------------------------------- #

    def _write_step(
        self,
        document: RequestDocument,
        step: Any,
        created_rows: Mapping[str, str],
        *,
        source: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Create the row for one step, resolving its references first.

        The order matters and is the researched data flow exactly: the CRM
        "resolves $1/@{refAccount.id} into real record URIs as it creates each
        row", so the values are substituted from what the *earlier* rows of this
        same run produced - and the resolved value is kept beside the sent one, so
        a reader can see the opportunity landed against the account that was just
        created rather than one that was already there.
        """
        sent = _sent_body(document, step)
        if document.dialect == DATAVERSE_BATCH:
            resolved = _resolve_dataverse_body(sent, document.plan, created_rows)
        else:
            resolved = _resolve_sf_body(sent, created_rows)
        if step.parent and step.parent.get("field"):
            resolved[step.parent["field"]] = created_rows.get(step.parent["reference"], "")

        record = self.store.create(
            TARGET_COLLECTION,
            {
                "object": step.record_type,
                "reference_id": step.reference_id,
                "run_id": self.run_id,
                "room_id": self.room_id,
                "body": resolved,
                "sent": sent,
            },
            room_id=self.room_id or None,
            actor=self.actor,
            source=source,
        )
        created_rows[step.reference_id] = record["id"]
        return sent, resolved, record["id"]

    def _link(self, record_id: str, parent_id: str, *, source: str) -> None:
        """Apply the association the rendered ``PUT`` asked for.

        The link is a field on the row rather than a separate record, so "is this
        contact attached to the account this bundle just created" is one
        ``?where=linked_to=…`` and not a join.
        """
        if parent_id and self.store.get(record_id) is not None:
            self.store.update(
                record_id, {"linked_to": parent_id}, actor=self.actor, source=source
            )

    # -- rollback and compensation ------------------------------------------ #

    def _rollback(
        self,
        created_rows: Mapping[str, str],
        outcomes: dict[str, StepOutcome],
        *,
        source: str,
    ) -> list[str]:
        """Delete every row this run created, in reverse order.

        Reverse because the researched dependency graph is a chain: deleting the
        Account before the Contact that points at it would leave the child briefly
        dangling, and a CRM with referential integrity would refuse.
        """
        rolled_back: list[str] = []
        for reference in reversed(list(created_rows)):
            self.store.delete(
                created_rows[reference], actor=self.actor, source=source, hard=True
            )
            rolled_back.append(reference)
            previous = outcomes[reference]
            outcomes[reference] = StepOutcome(
                reference_id=reference,
                record_type=previous.record_type,
                outcome=OUTCOME_ROLLED_BACK,
                depends_on=previous.depends_on,
                record_id=previous.record_id,
                reason="all_or_none",
                message=ROLLED_BACK_MESSAGE,
                sent=previous.sent,
                resolved=previous.resolved,
            )
        return rolled_back

    def _compensate(
        self,
        created_rows: Mapping[str, str],
        outcomes: dict[str, StepOutcome],
        *,
        source: str,
    ) -> tuple[list[str], list[str]]:
        """Undo a sequence by deleting, and say plainly when that is not enough.

        This is not a transaction and the run record does not claim it is: the
        dialect is not atomic, ``atomic`` is ``False`` on the document, and a
        delete this transport refuses is reported rather than swallowed.
        """
        rolled_back: list[str] = []
        failures: list[str] = []
        for reference in reversed(list(created_rows)):
            previous = outcomes[reference]
            if self.faults.get(f"{reference}-delete"):
                failures.append(reference)
                outcomes[reference] = StepOutcome(
                    reference_id=reference,
                    record_type=previous.record_type,
                    outcome=OUTCOME_CREATED,
                    depends_on=previous.depends_on,
                    record_id=previous.record_id,
                    reason="compensation_refused",
                    message=(
                        "The strict policy asked for this row to be deleted and the CRM "
                        "refused. A compensation is not a rollback, so the row remains."
                    ),
                )
                continue
            self.store.delete(
                created_rows[reference], actor=self.actor, source=source, hard=True
            )
            rolled_back.append(reference)
            outcomes[reference] = StepOutcome(
                reference_id=reference,
                record_type=previous.record_type,
                outcome=OUTCOME_ROLLED_BACK,
                depends_on=previous.depends_on,
                record_id=previous.record_id,
                reason="compensated",
                message=(
                    "Deleted by the strict policy's compensation. This dialect has no "
                    "documented transaction, so this is an undo, not a rollback."
                ),
                sent=previous.sent,
                resolved=previous.resolved,
            )
        return rolled_back, failures


# --------------------------------------------------------------------------- #
# Step helpers
# --------------------------------------------------------------------------- #


def _parent_after(step: Any, position_of: Mapping[str, int]) -> str | None:
    """The declared parent this step is about to run *before*, if any."""
    here = position_of.get(step.reference_id, 0)
    for parent in step.explicit_dependencies():
        if position_of.get(parent, 0) > here:
            return parent
    return None


def _blocked_by(step: Any, outcomes: Mapping[str, StepOutcome]) -> str | None:
    """The dependency that did not succeed, if any.

    Only *explicit* dependencies block. An implicit one is exactly the thing the
    research warns is not ordered against its dependency, so letting it block
    would be a guarantee the platform does not give - it is handled by
    :func:`_unmet_implicit` instead, which fires only when the ordering flag says
    the order is not guaranteed.
    """
    for parent in step.explicit_dependencies():
        outcome = outcomes.get(parent)
        if outcome is None or outcome.outcome in (
            OUTCOME_FAILED,
            OUTCOME_SKIPPED,
            OUTCOME_ROLLED_BACK,
        ):
            return parent
    return None


def _unmet_implicit(step: Any, plan: Any, created_rows: Mapping[str, str]) -> str | None:
    """The implicit dependency whose record does not exist yet, if any.

    This is the researched caveat, modelled: *"Collation can cause issues if
    there are implicit but not explicit dependencies between items. For example,
    consider a request that creates an Account, a Contact related to the Account,
    and a custom object that has a trigger dependent on the account name."* The
    trigger reads the account, so if the custom object is created first the
    trigger has nothing to read.

    It can only happen when the order is not guaranteed, so it is checked only
    on the one dialect that has the flag, and only while the flag is on. A
    dialect with no such flag either has no such grouping or takes the order
    structurally, and the planner has already said which in a warning.
    """
    if not step.implicit_depends_on:
        return None
    if plan.dialect != SALESFORCE_COMPOSITE or not plan.collate_subrequests:
        return None
    for reference in step.implicit_depends_on:
        if reference not in created_rows:
            return reference
    return None


def _part_steps(part: RequestPart, plan: Any) -> tuple[str, ...]:
    """Which declared steps one rendered part covers."""
    if part.kind == "batch-create":
        body = json.loads(part.body or "{}")
        return tuple(
            str(entry["id"]) for entry in body.get("inputs", []) if entry.get("id")
        )
    if part.kind == "association":
        return (part.reference_id.removesuffix("-association"),)
    return (part.reference_id,)


def _sent_body(document: RequestDocument, step: Any) -> dict[str, Any]:
    """The body that goes on the wire for this step, references unresolved.

    Read from the *rendered part* rather than from the step's declared fields,
    because the two differ and the difference is the whole point: the renderer
    writes the parent link, so a Dataverse part's body carries the ``$n`` bind
    and a composite subrequest carries the ``@{ref.id}`` placeholder. Recording
    the declared fields instead would make ``sent`` a field map, which is not what
    the name says and would hide the researched data flow from a reader of a run.
    """
    for part in document.parts:
        if part.kind == "batch-create":
            body = json.loads(part.body or "{}")
            for entry in body.get("inputs", []):
                if entry.get("id") == step.reference_id:
                    return dict(entry.get("properties") or {})
            continue
        if part.kind in ("changeset-part", "tree-record", "create", "collection"):
            if part.reference_id == step.reference_id and part.body:
                return json.loads(part.body).get("properties", json.loads(part.body))
    return dict(step.fields)


def _resolve_sf_body(body: Mapping[str, Any], created_rows: Mapping[str, str]) -> dict[str, Any]:
    """Substitute ``@{ref.path}`` from the rows this run created."""
    if not sf_references(body):
        return dict(body)
    return resolve_sf(
        dict(body), {reference: {"id": record_id} for reference, record_id in created_rows.items()}
    )


def _resolve_dataverse_body(
    body: Mapping[str, Any], plan: Any, created_rows: Mapping[str, str]
) -> dict[str, Any]:
    """Substitute ``$n`` with the URI the CRM handed back for that Content-ID."""
    from dsr.atomic_bundle.references import dv_references

    if not dv_references(body):
        return dict(body)
    numbers = {
        step.reference_id: content_id_for(step.reference_id, list(plan.order))
        for step in plan.steps
    }
    uris = {
        numbers[reference]: _uri(plan, reference, record_id)
        for reference, record_id in created_rows.items()
    }
    return resolve_dv(dict(body), uris)


def _uri(plan: Any, reference: str, record_id: str) -> str:
    """The record URI the CRM would have returned for a Content-ID."""
    step = plan.step(reference)
    name = (step.record_type if step is not None else "record").lower()
    if name.endswith(("s", "x", "ch", "sh")):
        name = f"{name}es"
    elif name.endswith("y") and len(name) > 1 and name[-2] not in "aeiou":
        name = f"{name[:-1]}ies"
    else:
        name = f"{name}s"
    return f"/api/data/v9.2/{name}({record_id})"


def _with_outcome(plan: Any, outcomes: Mapping[str, StepOutcome], outcome: str) -> list[str]:
    """The subrequests that ended up as ``outcome``, in the order declared.

    Every list on a result is in declared order, not execution order. A sequence
    dialect really does run the contacts batch create first, so an
    execution-ordered list would read as a different bundle than the one that was
    declared - and the one thing a run summary must not do is describe a bundle
    the rep did not write.
    """
    return [
        step.reference_id
        for step in plan.steps
        if step.reference_id in outcomes and outcomes[step.reference_id].outcome == outcome
    ]


def _actionable(
    outcomes: Mapping[str, StepOutcome], order: tuple[str, ...], failed: list[str]
) -> dict[str, Any] | None:
    """The single error the room shows.

    [sourced] "On any failure the whole bundle rolls back (strict mode) and the
    room shows a single actionable error." So this is one entry, not the whole
    list: the run record keeps every per-subrequest outcome for anyone reading
    the detail, and the room gets the first failure *in declared order*, which is
    the one whose fix unblocks the rest. A collation failure is chosen ahead of a
    plain refusal, because it is the one whose fix is a different setting.
    """
    if not failed:
        return None
    ranked = sorted(failed, key=lambda ref: order.index(ref) if ref in order else len(order))
    chosen = next(
        (ref for ref in ranked if outcomes[ref].reason == FAIL_COLLATION_VIOLATION), ranked[0]
    )
    first = outcomes[chosen]
    return {
        "reference_id": first.reference_id,
        "record_type": first.record_type,
        "reason": first.reason,
        "message": first.message,
        "detail": (
            f"Subrequest {first.reference_id} ({first.record_type}) did not commit. "
            f"{len(failed)} of {len(order)} subrequests failed."
        ),
    }


__all__ = [
    "CommitResult",
    "LocalCrm",
    "RESPONSE_NOT_PARSED_NOTE",
    "StepOutcome",
    "TARGET_COLLECTION",
    "Transport",
    "UrllibTransport",
]
