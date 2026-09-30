#!/usr/bin/env python3
"""Jev validation client: the single gate for every decision in this project.

Why this module exists
----------------------
Jev is a TypeSafe *System One* model. It does not generate prose, so it cannot be
spawned as an OpenCode sub-agent. It answers typed questions (``choice``,
``score``, ``noul``) and returns calibrated probabilities. This module is the
seam that turns those typed answers into a pass/fail gate that the Orchestrator
and its sub-agents can call without knowing anything about the Jev wire format.

Design contract
---------------
Every call follows the decision envelope required by the jev-assist skill:

    decision -> evidence/state -> questions -> verification

* ``decision``  what is being decided, and the pass threshold.
* ``state``     the observed evidence, as structured data.
* ``questions`` typed questions in the Jev request.
* ``verification`` the recorded answer, its probability, and the computed verdict.

Every decision is appended to an append-only JSONL audit log so a reviewer can
replay exactly what was judged, on what evidence, and with what confidence.

Transport
---------
Requests go to a local OpenAI-compatible bridge (``jev-bridge/server.mjs``) that
forwards them to ``https://api.typesafe.ai/v1/systemone``. The bridge is used
because it is the mechanism the operator configured; the payload written here is
a standard System One request, so pointing ``JEV_TRANSPORT=direct`` at the
TypeSafe endpoint directly requires no code change.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

BRIDGE_URL = os.environ.get("JEV_BRIDGE_URL", "http://127.0.0.1:8777/v1")
DIRECT_URL = os.environ.get("JEV_DIRECT_URL", "https://api.typesafe.ai/v1")
KEY_FILE = Path(
    os.environ.get(
        "JEV_KEY_FILE",
        Path(os.environ.get("USERPROFILE", Path.home())) / ".jev" / "bridge.env",
    )
)
BRIDGE_SCRIPT = Path(
    os.environ.get(
        "JEV_BRIDGE_SCRIPT", Path(os.environ.get("USERPROFILE", Path.home())) / "jev-bridge" / "server.mjs"
    )
)
AUDIT_LOG = Path(
    os.environ.get(
        "JEV_AUDIT_LOG",
        Path(__file__).resolve().parent.parent / "orchestration" / "decisions" / "jev-audit.jsonl",
    )
)
DEFAULT_MODEL = os.environ.get("JEV_MODEL", "jev-latest")

# Human overrides live beside the judgment log, never inside it: the judgment
# log is written by this module and by nothing else, and the point of an
# override is that a person wrote it. See ADR-0001.
OVERRIDE_LOG = Path(
    os.environ.get(
        "JEV_OVERRIDE_LOG",
        Path(__file__).resolve().parent.parent / "orchestration" / "decisions" / "human-overrides.jsonl",
    )
)

# A verdict is only enforced when confidence clears this bar. Below it the
# decision is recorded as ``uncertain`` and escalated rather than silently acted
# on. Tuned per use-case via ``Decision.threshold``.
DEFAULT_THRESHOLD = 0.75

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


class JevUnavailable(RuntimeError):
    """Raised when no transport to Jev can be established or used."""


# --------------------------------------------------------------------------- #
# Typed answers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Answer:
    """One typed Jev answer, normalised across the three primitives."""

    name: str
    type: str
    value: Any
    confidence: float | None
    probabilities: dict[str, float]
    legend: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, name: str, raw: dict[str, Any]) -> "Answer":
        kind = raw.get("type", "")
        if kind == "noul":
            value: Any = float(raw.get("noul", 0.0))
            confidence = None  # a Noul has no separate confidence field
        elif kind == "score":
            value = float(raw.get("score", 0.0))
            confidence = _opt_float(raw.get("confidence"))
        elif kind == "choice":
            value = raw.get("choice")
            confidence = _opt_float(raw.get("confidence"))
        else:
            value = raw.get("value")
            confidence = _opt_float(raw.get("confidence"))
        return cls(
            name=name,
            type=kind,
            value=value,
            confidence=confidence,
            probabilities={str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
            legend={str(k): str(v) for k, v in (raw.get("legend") or {}).items()},
        )

    def prob(self, option: str) -> float:
        """Probability assigned to one option/level. Missing means zero."""
        return float(self.probabilities.get(option, 0.0))


def _opt_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Decision envelope
# --------------------------------------------------------------------------- #


@dataclass
class Decision:
    """A recorded validation decision and the gate computed from it."""

    decision: str
    """What was decided, phrased so a reviewer can check the logic."""
    state: dict[str, Any]
    """The observed evidence handed to Jev."""
    answers: dict[str, Answer]
    model: str
    verdict: str
    passed: bool
    threshold: float
    reason: str
    usage: dict[str, int]
    latency_ms: float
    audit_id: str
    selected: str | None = None
    """The option Jev selected, when the gate was a selection between options."""
    questions: dict[str, Any] | None = None
    """The exact questions asked.

    Written from ADR-0003. Until then the log stored answers without the
    questions that produced them, so no two gates could be compared -- there was
    no way to tell a strict gate from a thin one.
    """
    pass_option: str = "verdict"
    """The question the verdict was derived from."""

    def summary(self) -> str:
        """Compact human-readable rendering for agent transcripts and logs."""
        lines = [
            f"decision : {self.decision}",
            f"verdict  : {self.verdict} (pass={self.passed}, threshold={self.threshold:.2f})",
            f"model    : {self.model}",
            f"reason   : {self.reason}",
        ]
        if self.selected is not None:
            lines.append(f"selected : {self.selected}")
        for answer in self.answers.values():
            conf = "-" if answer.confidence is None else f"{answer.confidence:.2f}"
            probs = ", ".join(f"{k}={v:.2f}" for k, v in sorted(answer.probabilities.items(), key=lambda kv: -kv[1]))
            lines.append(f"  - {answer.name} [{answer.type}] value={answer.value} confidence={conf}")
            if probs:
                lines.append(f"      {probs}")
        lines.append(f"audit_id : {self.audit_id}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decision": self.decision,
            "state": self.state,
            "model": self.model,
            "threshold": self.threshold,
            "verdict": self.verdict,
            "passed": self.passed,
            "selected": self.selected,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "pass_option": self.pass_option,
            "questions": self.questions or {},
            "answers": {
                name: {
                    "type": a.type,
                    "value": a.value,
                    "confidence": a.confidence,
                    "probabilities": a.probabilities,
                }
                for name, a in self.answers.items()
            },
        }



# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class Jev:
    """Client for typed validation decisions.

    One instance is cheap; connection reuse matters more than object identity,
    so long-lived callers should keep one instance per process.
    """

    def __init__(
        self,
        transport: str | None = None,
        model: str = DEFAULT_MODEL,
        audit_log: Path | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.transport = (transport or os.environ.get("JEV_TRANSPORT", "bridge")).lower()
        self.model = model
        self.audit_log = Path(audit_log) if audit_log else AUDIT_LOG
        self.timeout = timeout
        self._checked_bridge = False

    # -- transport ---------------------------------------------------------- #

    def _port_open(self, host_port: str, timeout: float = 1.5) -> bool:
        host, _, port = host_port.rpartition(":")
        try:
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def ensure_bridge(self) -> None:
        """Start the local bridge if it is not already listening.

        Idempotent and cheap after the first call. Failure is not fatal here:
        the caller may still be able to use the direct transport.
        """
        if self.transport != "bridge" or self._checked_bridge:
            return
        self._checked_bridge = True
        if self._port_open("127.0.0.1:8777"):
            return
        if not BRIDGE_SCRIPT.is_file():
            return
        try:
            subprocess.Popen(
                ["node", str(BRIDGE_SCRIPT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return
        for _ in range(20):
            if self._port_open("127.0.0.1:8777"):
                return
            time.sleep(0.25)

    def _api_key(self) -> str:
        if os.environ.get("TYPESAFE_API_KEY"):
            return os.environ["TYPESAFE_API_KEY"]
        if KEY_FILE.is_file():
            match = re.search(r"^TYPESAFE_API_KEY=(.*)$", KEY_FILE.read_text(), re.MULTILINE)
            if match:
                return match.group(1).strip().strip("\"'")
        raise JevUnavailable(
            "No Jev credential. Set TYPESAFE_API_KEY or create "
            f"{KEY_FILE} containing TYPESAFE_API_KEY=<key>."
        )

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def _call(self, systemone: dict[str, Any]) -> dict[str, Any]:
        """Send one System One request and return the raw System One result."""
        self.ensure_bridge()
        body = {"model": systemone.get("model", self.model), **systemone}

        if self.transport == "direct":
            result = self._post(
                f"{DIRECT_URL}/systemone",
                body,
                {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"},
            )
            return self._validate_result(result)

        payload = {
            "model": body["model"],
            "messages": [
                {
                    "role": "user",
                    "content": "```json\n" + json.dumps(body, indent=2) + "\n```",
                }
            ],
        }
        result = self._post(
            f"{BRIDGE_URL}/chat/completions", payload, {"Content-Type": "application/json"}
        )
        content = (result.get("choices") or [{}])[0].get("message", {}).get("content", "")
        match = _FENCE_RE.search(content)
        if not match:
            raise JevUnavailable(f"Bridge returned no typed result block: {content[:200]!r}")
        return self._validate_result(json.loads(match.group(1)))

    @staticmethod
    def _validate_result(result: dict[str, Any]) -> dict[str, Any]:
        answers = result.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise JevUnavailable(f"Jev returned no answers: {str(result)[:200]}")
        return result

    # -- public API --------------------------------------------------------- #

    def ask(self, state: Any, questions: dict[str, Any], model: str | None = None) -> dict[str, Answer]:
        """Ask typed questions and return normalised answers.

        No auditing or gating: use :meth:`decide` for anything that gates work.
        """
        result = self._call({"model": model or self.model, "state": state, "questions": questions})
        return {name: Answer.parse(name, raw) for name, raw in result["answers"].items()}

    def decide(
        self,
        decision: str,
        state: Any,
        questions: dict[str, Any],
        pass_option: str,
        threshold: float = DEFAULT_THRESHOLD,
        fail_options: Sequence[str] = (),
        model: str | None = None,
        gate: str = "option",
        pass_values: Sequence[str] = (),
    ) -> Decision:
        """Run a gated validation decision and audit the outcome.

        ``pass_option`` names the question that carries the gate.

        ``pass_values`` names the ``choice`` values that count as success for a
        ``gate="option"`` decision. It is separate from ``pass_option`` because
        the gate question is usually called ``verdict`` while the answer that
        passes is ``accept`` / ``ready`` / ``merge``. Leaving it empty falls
        back to ``(pass_option,)`` for callers whose passing value and question
        genuinely share a name.

        ``gate`` selects how that question is turned into a verdict:

        ``"option"``
            For approval gates. The question must be a ``choice`` and only
            ``pass_values`` counts as success. Any other selection fails, and a
            selection below ``threshold`` confidence is ``uncertain``.
        ``"confidence"``
            For selection gates where *any* offered option is a legitimate
            outcome, such as picking the best approach from a solution pool.
            Jev's selection is recorded as ``selected``; the gate passes when
            confidence clears ``threshold`` and is ``uncertain`` when it does
            not, so a near-tie is escalated instead of silently accepted.
        """
        if gate not in ("option", "confidence"):
            raise ValueError(f"unknown gate mode {gate!r}")
        started = time.perf_counter()
        result = self._call({"model": model or self.model, "state": state, "questions": questions})
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        answers = {name: Answer.parse(name, raw) for name, raw in result["answers"].items()}
        if pass_option not in answers:
            raise JevUnavailable(
                f"Gate question {pass_option!r} absent from answers: {sorted(answers)}"
            )
        gate_answer = answers[pass_option]
        verdict, passed, reason, selected = self._apply_gate(
            gate_answer, pass_option, fail_options, threshold, gate, pass_values
        )

        record = Decision(
            decision=decision,
            state=state if isinstance(state, dict) else {"state": state},
            answers=answers,
            model=result.get("model", model or self.model),
            verdict=verdict,
            passed=passed,
            threshold=threshold,
            reason=reason,
            usage={k: int(v) for k, v in (result.get("usage") or {}).items() if isinstance(v, (int, float))},
            latency_ms=latency_ms,
            audit_id="",
            selected=selected,
            questions=questions,
            pass_option=pass_option,
        )
        record.audit_id = self._audit(record)
        return record

    @staticmethod
    def _apply_gate(
        gate: Answer,
        pass_option: str,
        fail_options: Sequence[str],
        threshold: float,
        mode: str = "option",
        pass_values: Sequence[str] = (),
    ) -> tuple[str, bool, str, str | None]:
        """Return ``(verdict, passed, reason, selected)`` for a gate answer."""
        confidence = gate.confidence if gate.confidence is not None else 0.0
        pass_values = tuple(pass_values) or (pass_option,)

        if mode == "confidence":
            selected = gate.value if gate.type == "choice" else None
            if gate.type != "choice":
                return "fail", False, f"gate must be a choice for confidence gating, got {gate.type!r}", None
            spread = sorted(gate.probabilities.values(), reverse=True)
            margin = spread[0] - spread[1] if len(spread) > 1 else 1.0
            if confidence >= threshold:
                return (
                    "pass",
                    True,
                    f"selected {selected!r} at confidence {confidence:.2f} "
                    f"(margin {margin:.2f} over runner-up) >= {threshold:.2f}",
                    selected,
                )
            return (
                "uncertain",
                False,
                f"selected {selected!r} but confidence {confidence:.2f} < {threshold:.2f} "
                f"(margin {margin:.2f}); options too close to decide on this evidence",
                selected,
            )

        if gate.type == "choice":
            chosen = gate.value
            if chosen in fail_options:
                return "fail", False, f"chose {chosen!r} (explicit failure option)", chosen
            if chosen not in pass_values:
                return (
                    "fail",
                    False,
                    f"chose {chosen!r}; only {', '.join(repr(v) for v in pass_values)} passes",
                    chosen,
                )
            if confidence >= threshold:
                return "pass", True, f"chose {chosen!r} at confidence {confidence:.2f}", chosen
            return (
                "uncertain",
                False,
                f"chose {chosen!r} but confidence {confidence:.2f} < {threshold:.2f}; "
                "insufficient evidence to gate on",
                chosen,
            )

        # Noul / Score: pass is a probability at or above the threshold.
        try:
            probability = float(gate.value)
        except (TypeError, ValueError):
            return "fail", False, f"gate answer {gate.value!r} is not numeric", None
        if probability >= threshold:
            return "pass", True, f"probability {probability:.2f} >= {threshold:.2f}", None
        return "fail", False, f"probability {probability:.2f} < {threshold:.2f}", None


    def _audit(self, record: Decision) -> str:
        """Append the decision to the JSONL audit log and return its id."""
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc)
        audit_id = f"jev-{stamp.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{int(time.time() * 1000) % 100000:05d}"
        record.audit_id = audit_id
        payload = record.to_dict()
        payload["decided_at"] = stamp.isoformat()
        with self.audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return audit_id

    # -- high-level gates used across the project --------------------------- #

    def validate_workflow(
        self,
        workflow_id: str,
        name: str,
        evidence: str,
        sources: Iterable[str] = (),
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Decision:
        """Gate a researched workflow for relevance and evidential grounding."""
        state = {
            "workflow_id": workflow_id,
            "name": name,
            "observed_evidence": evidence,
            "cited_sources": list(sources),
            "requirement": "A distinct, evidenced Digital Sales Room workflow that can be implemented and verified locally.",
        }
        return self.decide(
            decision=f"Workflow {workflow_id} ({name}) is relevant, distinct, and evidenced",
            state=state,
            questions={
                "is_dsr_workflow": {
                    "type": "noul",
                    "instructions": "Does the described capability belong to a Digital Sales Room (buyer-facing or seller-facing sales engagement software)?",
                    "criteria": {
                        "true": "A sales-engagement or buying-experience capability",
                        "false": "Unrelated to sales engagement or buying experience",
                    },
                },
                "is_evidenced": {
                    "type": "noul",
                    "instructions": "Does the evidence show this workflow exists in real products, with concrete features rather than generic guesses?",
                    "criteria": {
                        "true": "Concrete features, flows, or API behaviour are described",
                        "false": "Generic or speculative with no concrete detail",
                    },
                },
                "verdict": {
                    "type": "choice",
                    "instructions": "Given the evidence, should this workflow enter the design phase?",
                    "criteria": {
                        "accept": "Evidenced and relevant, ready for design",
                        "revise": "Relevant but evidence is thin, needs more research",
                        "reject": "Not a sales-room workflow, or indistinguishable from an existing one",
                    },
                },
            },
            pass_option="verdict",
            pass_values=("accept",),
            fail_options=("reject",),
            threshold=threshold,
        )

    def validate_design(
        self,
        workflow_id: str,
        document: str,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Decision:
        """Gate a Technical Design Doc / Spec for being coding-ready."""
        state = {
            "workflow_id": workflow_id,
            "document": document,
            "stack": "React + Tailwind frontend, Python backend, SQLite with audited writes, schema-flexible APIs",
            "requirement": "A coding agent must be able to implement this without asking follow-up questions.",
        }
        return self.decide(
            decision=f"Design doc for {workflow_id} is complete enough to hand to a coding agent",
            state=state,
            questions={
                "verdict": {
                    "type": "choice",
                    "instructions": "Does this document specify the user flow, data flow, APIs, automations, and tests concretely enough to implement without clarification?",
                    "criteria": {
                        "ready": "Every required area is specified concretely; no clarification needed",
                        "gaps": "Some required area is vague or missing",
                        "unusable": "Too incomplete to implement",
                    },
                }
            },
            pass_option="verdict",
            pass_values=("ready",),
            fail_options=("unusable",),
            threshold=threshold,
        )

    def score_implementation(
        self,
        ticket: str,
        diff_summary: str,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Decision:
        """Gate a merged feature branch on cleanliness, tests, and API surface."""
        state = {
            "ticket": ticket,
            "change_summary": diff_summary,
            "requirements": [
                "React + Tailwind UI",
                "Python backend",
                "SQLite access only through the audited wrapper",
                "Schema-flexible API for every layer",
                "Tests present and passing",
            ],
        }
        return self.decide(
            decision=f"Implementation of {ticket} meets the release bar",
            state=state,
            questions={
                "meets_bar": {
                    "type": "noul",
                    "instructions": "Does this change satisfy every listed requirement: the stated UI stack, a Python backend, SQLite writes only through the audited wrapper, a schema-flexible API, and passing tests?",
                    "criteria": {
                        "true": "All requirements are demonstrably met",
                        "false": "At least one requirement is unmet or unverified",
                    },
                },
                "verdict": {
                    "type": "choice",
                    "instructions": "Should this branch be merged?",
                    "criteria": {
                        "merge": "Ready to merge",
                        "fix": "Needs a fix-up pass, specifics are named in the state",
                        "reject": "Fundamentally off-spec, needs rework",
                    },
                },
            },
            pass_option="verdict",
            pass_values=("merge",),
            fail_options=("reject",),
            threshold=threshold,
        )

    def release_bar(
        self,
        ticket: str,
        change_summary: str,
        measurements: dict[str, Any] | None = None,
        unverified: Sequence[str] = (),
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Decision:
        """The pinned release bar. See ``docs/adr/0003-the-release-bar-question-set-is-pinned.md``.

        Every landing is asked these same questions, so two gates can be compared
        even when they ran on different tickets. The previous freeform payloads
        stored their answers but not their questions, which made the log
        unauditable.

        ``measurements`` is the machine-read half of the evidence: suite tallies,
        the guard's changed-file count, the feature's route count, the design
        floor's result. ``unverified`` is the honest half: anything a person
        looked at and did not measure.

        The evidence was re-specified after five features scored 0.11 to 0.23 on
        the original single question while the other four scored 0.84 to 0.95.
        The cause was that one question asked both whether claims were measured
        *and* how much was measured, so a change that declared its own limits
        honestly -- one browser engine, no screen reader, code recovered from a
        base 50 commits behind -- was scored as missing evidence. A check that
        punishes disclosure trains people to conceal, which is worse than having
        no check. So it is now two questions: ``claims_backed_by_measurement``
        asks whether each claim traces to output that was actually produced, and
        ``coverage_of_the_change`` asks the breadth question on its own terms.
        Declared limits stop being a defect and become context.
        """
        state = {
            "ticket": ticket,
            "change_summary": change_summary,
            "measurements": measurements or {},
            "declared_unverified": list(unverified),
            "requirements": (
                "A feature adds files and edits no shared file. All reads and writes go through "
                "dsr.deps / RecordStore / AuditedDatabase so every mutation writes its audit row in "
                "the same transaction. Payloads stay schema-flexible JSON: no migration, no typed "
                "column, the envelope untouched. The feature owns a unique route prefix with no "
                "colliding concrete (method, path) pair. Tests exist and the whole suite is green. "
                "The frontend page builds, is discovered by the glob, and meets the design floor."
            ),
        }
        return self.decide(
            decision=f"Implementation of {ticket} meets the release bar",
            state=state,
            questions={
                "contract_compliance": {
                    "type": "noul",
                    "instructions": "Does this change add only new files and leave every shared file untouched, per the feature contract?",
                    "criteria": {
                        "true": "No shared file is edited, and the contract guard confirms it on a non-empty diff",
                        "false": "A shared file is edited, or the guard did not actually measure a diff",
                    },
                },
                "claims_backed_by_measurement": {
                    "type": "noul",
                    "instructions": (
                        "Is every claim made in the evidence supported by a measurement that was "
                        "actually run, rather than asserted, read off a progress bar, or produced "
                        "by a check that passed on empty input? Declared limits are not a failure "
                        "of this question -- they are the honest disclosure that belongs to them."
                    ),
                    "criteria": {
                        "true": "Each claim traces to quoted, reproducible tool output",
                        "false": "A claim is asserted, or a check passed vacuously or was not run",
                    },
                },
                "coverage_of_the_change": {
                    "type": "noul",
                    "instructions": (
                        "Setting aside what is declared unverified, how much of the change did the "
                        "evidence actually exercise? Judge breadth, not honesty: a declared limit "
                        "is fine, but a large unexamined surface is not."
                    ),
                    "criteria": {
                        "true": "Every surface the change introduces is exercised by a measurement, with at most incidental limits declared",
                        "false": "A substantial part of the change was never measured, whether or not that is declared",
                    },
                },
                "audit_integrity": {
                    "type": "noul",
                    "instructions": "Does this change preserve the audited-store guarantee: all writes through the wrapper, no direct database connection, no schema change outside the envelope?",
                    "criteria": {
                        "true": "Writes go through the audited store and the envelope is unchanged",
                        "false": "A direct connection is opened, or a typed column or migration is added",
                    },
                },
                "design_floor": {
                    "type": "noul",
                    "instructions": "Does the frontend meet the design floor -- 44px touch targets, visible focus, a text label beside every icon, no emoji as icons, reduced motion respected -- as checked by the scripted pass?",
                    "criteria": {
                        "true": "The scripted design-floor pass is green",
                        "false": "The pass is red, was skipped, or is on the allowlist",
                    },
                },
                "verdict": {
                    "type": "choice",
                    "instructions": (
                        "Should this change land on main? Weigh the balance of the questions above "
                        "rather than deciding on your own: where contract_compliance, "
                        "audit_integrity and design_floor are all healthy and the evidence "
                        "questions show broad coverage, answer merge with the confidence that "
                        "warrants. Items listed as unverified are declared context to weigh, not "
                        "defects in themselves -- but a change whose coverage is genuinely thin, "
                        "or which was recovered from a base far behind main and not reconciled, "
                        "is fix."
                    ),
                    "criteria": {
                        "merge": "Evidence supports landing it: suite green, contract intact, floor met",
                        "fix": "Worth keeping but needs work against current main before landing",
                        "reject": "Should not land",
                    },
                },
            },
            pass_option="verdict",
            pass_values=("merge",),
            fail_options=("fix", "reject"),
            threshold=threshold,
        )

    def choose_approach(
        self,
        problem: str,
        options: dict[str, str],
        context: dict[str, Any] | None = None,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Decision:
        """Pick the best option from a solution pool for an open decision.

        This is the mechanism for hot topics: hand over the problem and the
        candidate solutions, and let the calibrated model choose rather than
        guessing. ``options`` maps option id to its description.
        """
        if len(options) < 2:
            raise ValueError("choose_approach needs at least two options to compare")
        state = {
            "problem": problem,
            "candidate_solutions": options,
            "context": context or {},
        }
        return self.decide(
            decision=f"Which approach best resolves: {problem}",
            state=state,
            questions={
                "best_approach": {
                    "type": "choice",
                    "instructions": "Considering the stated problem and context, which candidate solution best resolves it? Judge each candidate on whether it actually solves the stated problem, its risk, and its reversibility.",
                    "criteria": options,
                },
                "is_confident": {
                    "type": "noul",
                    "instructions": "Does the evidence support a decisive choice between these candidates?",
                    "criteria": {
                        "true": "One candidate is clearly better on the stated problem",
                        "false": "The candidates are close, or the problem is under-specified",
                    },
                },
            },
            pass_option="best_approach",
            threshold=threshold,
            gate="confidence",
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask ad-hoc typed questions from a JSON file")
    ask.add_argument("payload", type=Path, help="JSON file: {state, questions}")
    ask.add_argument("--transport", default="bridge", choices=("bridge", "direct"))
    ask.add_argument("--model", default=DEFAULT_MODEL)

    decide = sub.add_parser("decide", help="run a gated decision from a JSON file")
    decide.add_argument(
        "payload",
        type=Path,
        help="JSON: {decision, state, questions, pass_option, pass_values?, fail_options?}",
    )
    decide.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    decide.add_argument("--transport", default="bridge", choices=("bridge", "direct"))
    decide.add_argument("--model", default=DEFAULT_MODEL)

    doctor = sub.add_parser("doctor", help="check that Jev is reachable and answering")
    doctor.add_argument("--transport", default="bridge", choices=("bridge", "direct"))

    bar = sub.add_parser(
        "release-bar",
        help="gate a landing with the pinned question set (ADR-0003)",
    )
    bar.add_argument("ticket", help="e.g. WF-033")
    bar.add_argument("payload", type=Path, help="JSON: {change_summary, measurements?, unverified?}")
    bar.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    bar.add_argument("--transport", default="bridge", choices=("bridge", "direct"))
    bar.add_argument("--model", default=DEFAULT_MODEL)

    override = sub.add_parser(
        "override",
        help="record a human override for a landing the judgment did not clear",
    )
    override.add_argument(
        "payload",
        type=Path,
        help="JSON: {ticket, audit_id?, unmet_criteria, rationale, closes, decided_by}",
    )
    override.add_argument(
        "--as",
        dest="decided_by",
        required=False,
        help="the person taking responsibility; must not be an agent",
    )

    args = parser.parse_args(argv)

    if args.command == "override":
        return _record_override(args)

    client = Jev(transport=args.transport, model=getattr(args, "model", DEFAULT_MODEL))

    if args.command == "release-bar":
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
        record = client.release_bar(
            ticket=args.ticket,
            change_summary=payload["change_summary"],
            measurements=payload.get("measurements", {}),
            unverified=payload.get("unverified", ()),
            threshold=args.threshold,
        )
        print(record.summary())
        return 0 if record.passed else 1

    if args.command == "doctor":
        transport = args.transport
        try:
            answers = client.ask(
                state="Connectivity probe for the Digital Sales Room validation pipeline.",
                questions={
                    "reachable": {
                        "type": "noul",
                        "instructions": "Is this text a Digital Sales Room project validation notice?",
                        "criteria": {"true": "It is", "false": "It is not"},
                    }
                },
            )
        except JevUnavailable as exc:
            print(f"Jev unavailable via {transport}: {exc}", file=sys.stderr)
            return 2
        probe = answers["reachable"]
        print(f"transport={transport} model={client.model} type={probe.type} value={probe.value}")
        return 0

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if args.command == "ask":
        answers = client.ask(
            state=payload["state"],
            questions=payload["questions"],
            model=args.model,
        )
        for name, answer in answers.items():
            print(
                f"{name}: type={answer.type} value={answer.value} "
                f"confidence={answer.confidence} probabilities={answer.probabilities}"
            )
        return 0

    record = client.decide(
        decision=payload["decision"],
        state=payload["state"],
        questions=payload["questions"],
        pass_option=payload.get("pass_option", "verdict"),
        pass_values=payload.get("pass_values", ()),
        fail_options=payload.get("fail_options", ()),
        threshold=args.threshold,
        model=args.model,
    )
    print(record.summary())
    return 0 if record.passed else 1


# --------------------------------------------------------------------------- #
# Human override
# --------------------------------------------------------------------------- #

OVERRIDE_FIELDS = ("ticket", "unmet_criteria", "rationale", "closes", "decided_by")

# Words that name a program rather than a person. The party that wants the
# change landed is never the party that clears it.
_AGENT_MARKERS = {
    "agent", "assistant", "bot", "orchestrator", "ai", "llm", "model",
    "automation", "script", "ci", "system", "copilot", "cursor", "codex",
    "commandcode", "command code", "claude", "chatgpt", "gpt", "opencode",
}


def _record_override(args) -> int:
    """Append a human override. Four fields are mandatory; see ADR-0001."""
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    decided_by = args.decided_by or payload.get("decided_by") or ""
    decided_by = decided_by.strip()

    if not decided_by:
        print("An override needs a named human in --as or decided_by.", file=sys.stderr)
        return 2
    if decided_by.lower() in _AGENT_MARKERS:
        print(
            f"Refusing: {decided_by!r} is not a person. ADR-0001 reserves overrides "
            "for humans, so that the party wanting the landing is not the party "
            "clearing it. See docs/adr/0001-gate-authority-human-override.md",
            file=sys.stderr,
        )
        return 2

    missing = [f for f in ("unmet_criteria", "rationale", "closes") if not payload.get(f)]
    if missing:
        print(f"An override must record: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not payload.get("ticket"):
        print("An override must name the ticket it applies to.", file=sys.stderr)
        return 2

    entry = {
        "override_id": f"override-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "ticket": payload["ticket"],
        "audit_id": payload.get("audit_id"),
        "verdict_overridden": payload.get("verdict_overridden"),
        "unmet_criteria": payload["unmet_criteria"],
        "rationale": payload["rationale"],
        "closes": payload["closes"],
        "decided_by": decided_by,
    }
    OVERRIDE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OVERRIDE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"override recorded for {entry['ticket']}")
    print(f"  file     : {OVERRIDE_LOG}")
    print(f"  decided  : {decided_by}")
    print(f"  unmet    : {entry['unmet_criteria']}")
    print(f"  closes   : {entry['closes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
