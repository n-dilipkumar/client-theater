"""WF-135: capture the deal conversation into the CRM through one entry.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-135.md``
and issue 177 quotes it in full. This module is the domain of one workflow: a
recorded conversation is transcribed and extracted somewhere else, its four
outputs arrive here as data, and one write fans them out to the room's notes, a
handoff packet and the CRM custom fields so the rep enters the data once.

What the research fixes, and what this build chose
--------------------------------------------------

The research is explicit that this workflow is a **glue problem with no single
open-source answer** and that there is **no open-source MEDDICC-enforcing
conversation-intelligence product of note**. So the build separates the sourced
steps from the chosen ones and writes each choice down in :data:`DECISIONS`:

* **Sourced.** The pipeline (transcription, then extraction, then a fan-out) and
  the four extraction outputs: a summary, the next steps, the requirements and
  the MEDDICC evidence. The three destinations: the room's notes, the handoff
  packet and the CRM custom fields. The named CRMs (Salesforce, HubSpot,
  Bullhorn) come from one source, Ebsta.
* **Chosen.** Transcription and extraction are **not** built here: they arrive
  through this feature's API as data, so the module stays importable and
  testable with no network. The CRM write is modelled as data with a state,
  because this repository holds no live CRM connection. MEDDICC evidence is
  captured per letter with a coverage report and **no score**, because the
  research names no scale.

The MAP, and one-way or two-way
-------------------------------

The sourced claim is that "the room, the MAP and the CRM all agree". In this
repository the MAP is the WF-124 Mutual Action Plan. Its id is recorded on the
capture as a **reference**; this build does not write into WF-124's own
collections. The fan-out reaches the three destinations this feature owns, and
agreement is checked across those three.

"One-time data entry" is a vendor phrase and it promises **bi-directional** sync.
The data flow describes only a fan-out, and this repository has no CRM
subscription, no webhook receiver for this workflow and no conflict rule for the
same field written twice, so only the outbound half is built and the reverse half
is recorded as not built.

The import surface
------------------

The package imports the store and its own modules and nothing else. The enforced
suite test over ``dsr.competitive_baseline`` allows no other ``dsr`` import, so
the CRM outcome vocabulary is re-declared here with a note naming its source
(WF-038) rather than imported.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections. One shared `records` table carries every feature, so each name is
# namespaced per ticket and `find()` matches on the collection first.
# --------------------------------------------------------------------------- #

#: One captured conversation: the extraction and the ids of its fan-out rows.
CAPTURE_COLLECTION = "wf135_capture"

#: One fan-out row: the room's notes, the handoff packet or the CRM fields.
DESTINATION_COLLECTION = "wf135_destination"

#: The payload-side twin of the envelope's `room_id`. `room_id` is stripped from
#: `data` before the dynamic index is built, so a payload that stored its room
#: there would be unfilterable by `find()`.
ROOM_REF = "room_ref"

#: The payload-side pointer from a destination back to its capture.
CAPTURE_REF = "capture_ref"

# --------------------------------------------------------------------------- #
# Where a conversation comes from, and what the extraction yields
# --------------------------------------------------------------------------- #

#: The researched data sources: recorded calls and meetings, email threads,
#: calendar events and the rep's spoken playbook. The deal record is the room the
#: capture is attached to, so it is the `room_ref` rather than a source kind.
SOURCE_KINDS = ("call", "meeting", "email_thread", "calendar_event", "spoken_playbook")

#: The source kind used when the payload does not name one.
DEFAULT_SOURCE_KIND = "call"

#: The four outputs the sourced data flow names: "summary, next steps,
#: requirements and MEDDICC evidence". Four outputs, not one.
EXTRACTION_OUTPUTS = ("summary", "next_steps", "requirements", "meddicc")

#: MEDDICC, letter by letter. The research names "MEDDICC evidence" without
#: expanding it, so the expansion below is the conventional sales-methodology one
#: and is recorded as a chosen vocabulary rather than a sourced one.
MEDDICC_LETTERS: tuple[tuple[str, str, str], ...] = (
    ("metrics", "M", "Metrics"),
    ("economic_buyer", "E", "Economic buyer"),
    ("decision_criteria", "D", "Decision criteria"),
    ("decision_process", "D", "Decision process"),
    ("identify_pain", "I", "Identify pain"),
    ("champion", "C", "Champion"),
    ("competition", "C", "Competition"),
)

#: The MEDDICC keys, in order, for a coverage report that does not recompute them.
MEDDICC_KEYS = tuple(key for key, _letter, _label in MEDDICC_LETTERS)

#: A key -> label map, so a page labels a letter without repeating the table.
MEDDICC_LABELS = {key: label for key, _letter, label in MEDDICC_LETTERS}

# --------------------------------------------------------------------------- #
# The three destinations, and their states
# --------------------------------------------------------------------------- #

#: The room's running notes.
ROOM_NOTE = "room_note"

#: The packet post-sales reads instead of re-listening to the calls.
HANDOFF_PACKET = "handoff_packet"

#: The CRM custom fields the extraction writes.
CRM_FIELD = "crm_field"

#: Every destination the fan-out reaches. One entry reaches all of them.
DESTINATION_KINDS = (ROOM_NOTE, HANDOFF_PACKET, CRM_FIELD)

#: A human label per destination kind.
DESTINATION_LABELS = {
    ROOM_NOTE: "Room notes",
    HANDOFF_PACKET: "Handoff packet",
    CRM_FIELD: "CRM custom fields",
}

#: This workflow wrote the destination row.
WRITTEN = "written"

#: This workflow wrote the destination row and a later report marked it failed.
FAILED = "failed"

#: Every state a destination row can be in.
DESTINATION_STATES = (WRITTEN, FAILED)

#: The CRM outcome vocabulary, re-declared from WF-038
#: (``dsr/crm_upsert/payloads.py::OUTCOMES``) with the same names and meanings.
#: Re-declared rather than imported because a module in this package may import
#: only the store, and a second, differently-worded set would leave two
#: vocabularies for one idea in the product.
CRM_OUTCOMES = (
    "created",  # the key was not found and a new record was created
    "updated",  # the key was found and the record was updated
    "submitted",  # sent; the vendor returns no per-item result to confirm it
    "failed",  # the vendor returned an error for this row
    "rejected",  # refused here, before any request was sent
    "rolled_back",  # a sibling row failed under allOrNone, so nothing was written
)

#: The outcomes that mean the CRM accepted the write.
CRM_SUCCESS_OUTCOMES = ("created", "updated", "submitted")

#: The outcomes that mean the CRM did not accept the write.
CRM_FAILURE_OUTCOMES = tuple(name for name in CRM_OUTCOMES if name not in CRM_SUCCESS_OUTCOMES)

#: The state of a CRM field payload before anyone reports the outbound send. This
#: build makes no call to a CRM, so it is honest that nothing left the process.
CRM_NOT_SENT = "not_sent"

#: The report keys a destination accepts, by kind. A CRM field reports a CRM
#: outcome; the two local destinations report this workflow's own two states.
REPORTABLE_OUTCOMES: dict[str, tuple[str, ...]] = {
    ROOM_NOTE: DESTINATION_STATES,
    HANDOFF_PACKET: DESTINATION_STATES,
    CRM_FIELD: CRM_OUTCOMES,
}

# --------------------------------------------------------------------------- #
# The CRM custom fields
# --------------------------------------------------------------------------- #

#: The default map from a logical field to a CRM custom-field API name. A payload
#: may override it with its own ``field_map``, so a team names its own fields
#: without a migration.
DEFAULT_CRM_FIELDS = {
    "summary": "Opportunity.Conversation_Summary__c",
    "next_steps": "Opportunity.Next_Steps__c",
    "requirements": "Account.Requirements__c",
}

#: How the CRM custom fields are separated when a list is collapsed into one
#: field. A pipe rather than a newline, so the value survives one line in a CRM.
CRM_LIST_SEPARATOR = " | "

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class CaptureError(ValueError):
    """A capture, a report or an extraction this workflow will not accept.

    Carries a field-keyed map, because a rep filling in a form needs the message
    beside the input that caused it. The HTTP layer renders the map as ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class CaptureRefused(PermissionError):
    """A change the capture's current state will not accept.

    Raised when the fan-out does not reach every destination, and when a write
    does not read back as written. ``reason`` is a stable machine token, so the
    page can branch on it instead of parsing an English sentence.
    """

    #: The fan-out reached fewer destinations than the workflow requires.
    REASON_FANOUT_INCOMPLETE = "fanout_incomplete"

    #: A write was made but did not read back. The workflow must not report success.
    REASON_WRITE_NOT_VISIBLE = "write_not_visible"

    _MESSAGES = {
        REASON_FANOUT_INCOMPLETE: (
            "The fan-out reached fewer destinations than the workflow requires, so the "
            "capture is not complete."
        ),
        REASON_WRITE_NOT_VISIBLE: "The change was written but did not read back.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


class CaptureNotFound(LookupError):
    """No such capture or fan-out row.

    Its own type rather than the store's ``RecordNotFound``, because a feature may
    map only the error types it raises itself. Registering a handler for the
    shared type would intercept that exception across the whole product.
    """


# --------------------------------------------------------------------------- #
# Deciding what changes
# --------------------------------------------------------------------------- #


def validate_capture(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read a capture payload into the shape this workflow stores.

    Raises :class:`CaptureError` with a field-keyed map when a required value is
    missing or a value cannot be read. A field this workflow has never heard of
    is kept, so a team adds its own without a migration.
    """
    data = dict(payload or {})
    errors: dict[str, str] = {}

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors["summary"] = "The extraction must carry a summary."

    source_kind = data.get("source_kind") or DEFAULT_SOURCE_KIND
    if source_kind not in SOURCE_KINDS:
        errors["source_kind"] = f"Source kind must be one of: {', '.join(SOURCE_KINDS)}."

    occurred_at = data.get("occurred_at")
    if occurred_at is not None and _parse_when(occurred_at) is None:
        errors["occurred_at"] = "The conversation time must be an ISO 8601 date and time."

    next_steps = _read_items(data.get("next_steps"), "next_steps", errors)
    requirements = _read_items(data.get("requirements"), "requirements", errors)
    meddicc = _read_meddicc(data.get("meddicc"), errors)
    field_map = _read_field_map(data.get("field_map"), errors)

    if errors:
        raise CaptureError("The capture could not be read.", errors)

    return {
        **data,
        "summary": summary.strip(),
        "source_kind": source_kind,
        "occurred_at": occurred_at,
        "next_steps": next_steps,
        "requirements": requirements,
        "meddicc": meddicc,
        "field_map": field_map,
    }


def validate_report(kind: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read a destination report into ``{"outcome", "note"}``.

    A CRM field reports a :data:`CRM_OUTCOMES` value; the two local destinations
    report :data:`DESTINATION_STATES`. An outcome from the wrong set is refused
    rather than stored, so one idea does not grow two vocabularies.
    """
    body = dict(payload or {})
    outcome = body.get("outcome")
    allowed = REPORTABLE_OUTCOMES.get(kind, ())
    if outcome not in allowed:
        raise CaptureError(
            "The report could not be read.",
            {"outcome": f"Outcome must be one of: {', '.join(allowed)}."},
        )
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise CaptureError("The report could not be read.", {"note": "The note must be text."})
    return {"outcome": outcome, "note": (note or "").strip()}


def validate_destination_kind(kind: str) -> str:
    """Every destination this workflow writes carries a kind it knows."""
    if kind not in DESTINATION_KINDS:
        raise CaptureError(
            "The destination could not be read.",
            {"kind": f"Kind must be one of: {', '.join(DESTINATION_KINDS)}."},
        )
    return kind


# --------------------------------------------------------------------------- #
# Coverage and agreement: the two derived readings
# --------------------------------------------------------------------------- #


def covered_letters(extraction: Mapping[str, Any] | None) -> list[str]:
    """The MEDDICC letters this extraction carries evidence for.

    A letter counts when it holds at least one non-empty evidence line. No
    weighting and no score: the research names MEDDICC evidence as an output and
    names no scale.
    """
    meddicc = (extraction or {}).get("meddicc") or {}
    covered: list[str] = []
    for key in MEDDICC_KEYS:
        if _evidence_lines(meddicc.get(key)):
            covered.append(key)
    return covered


def agreement_of(kind_states: Mapping[str, str]) -> dict[str, Any]:
    """Whether one entry reached every destination without a failure.

    ``kind_states`` maps a destination kind to its state. A destination that was
    never written counts as missing, so a fan-out that reached one destination
    has not completed the workflow.
    """
    written = sorted(kind for kind, state in kind_states.items() if state == WRITTEN)
    failed = sorted(kind for kind, state in kind_states.items() if state == FAILED)
    missing = sorted(kind for kind in DESTINATION_KINDS if kind not in kind_states)
    return {
        "agreed": not failed and not missing and len(written) == len(DESTINATION_KINDS),
        "written": written,
        "failed": failed,
        "missing": missing,
    }


def crm_field_values(extraction: Mapping[str, Any], field_map: Mapping[str, str]) -> dict[str, str]:
    """Collapse the extraction into the CRM custom fields the fan-out writes.

    A list becomes one piped line so the value survives a single CRM field. The
    MEDDICC evidence is not a field here: the research names it as an output of
    the extraction, and it travels in the handoff packet, not as a CRM field.
    """
    joined_steps = CRM_LIST_SEPARATOR.join(
        _item_text(item) for item in extraction.get("next_steps", [])
    )
    joined_requirements = CRM_LIST_SEPARATOR.join(
        _item_text(item) for item in extraction.get("requirements", [])
    )
    values = {
        "summary": extraction.get("summary", ""),
        "next_steps": joined_steps,
        "requirements": joined_requirements,
    }
    return {field_map[key]: value for key, value in values.items() if field_map.get(key)}


# --------------------------------------------------------------------------- #
# The vocabulary, decisions and what this build does not do
# --------------------------------------------------------------------------- #

DECISIONS: dict[str, str] = {
    "transcription": (
        "Transcription is not built here. The research names self-hosted Whisper as "
        "the open-source option for a self-hosted pipeline; this build takes the "
        "transcript reference through its own API so the module imports and tests "
        "with no network and does not imply a vendor parity it does not have."
    ),
    "extraction": (
        "Extraction is not built here. The research names an LLM for the extraction "
        "and names no vendor; the four outputs (summary, next steps, requirements, "
        "MEDDICC evidence) arrive as data, and this build validates, stores and fans "
        "them out."
    ),
    "meddicc": (
        "MEDDICC evidence is captured per letter with a coverage report and no score. "
        "The research records that there is no open-source MEDDICC-enforcing "
        "conversation-intelligence product of note, so any total would be an invented "
        "scale that reads as a standard."
    ),
    "fan_out": (
        "One entry fans out to the three destinations this feature owns: the room's "
        "notes, the handoff packet and the CRM custom fields. A write that reaches "
        "one destination has not completed the workflow, so the fan-out writes all "
        "three and reads them back. Chosen by Jev, audit "
        "jev-20261014T093843-10440-23148, verdict pass."
    ),
    "map": (
        "MAP means the WF-124 Mutual Action Plan. Its id is recorded on the capture as "
        "a reference; this build does not write into WF-124's plan or task "
        "collections, so a WF-124 reshape cannot break this workflow. Same Jev audit "
        "jev-20261014T093843-10440-23148."
    ),
    "direction": (
        "The write is one way out, to the three destinations. The vendor phrase "
        "'bi-directional CRM integrations' promises the reverse half too; this "
        "repository holds no CRM subscription, no webhook receiver for this workflow "
        "and no conflict rule for the same field written twice, so the reverse half "
        "is not built. Chosen by Jev, audit jev-20261014T093916-11192-56740, verdict "
        "pass."
    ),
    "crm_vocabulary": (
        "The CRM per-row outcome names (created, updated, submitted, failed, "
        "rejected, rolled_back) are WF-038's, from dsr/crm_upsert/payloads.py. They "
        "are re-declared here rather than imported because a module in this package "
        "may import only the store, and a second differently-worded set would leave "
        "two vocabularies for one idea in the product."
    ),
    "agreement": (
        "Agreement is checked across the three destinations this feature owns. A "
        "destination reported failed makes the capture partial, and a capture whose "
        "fan-out is missing a destination is refused rather than called complete."
    ),
    "extraction_is_data": (
        "The caller posts the transcript reference and the four extraction outputs. "
        "Chosen by Jev, audit jev-20261014T094017-15972-17889, verdict pass."
    ),
    "crm_connection": (
        "This build makes no call to a CRM. The CRM custom fields are staged as data "
        "with a state and the outbound outcome is reported by the caller, so the "
        "workflow is testable with no network."
    ),
}

NOT_IMPLEMENTED: tuple[str, ...] = (
    "Live transcription (self-hosted Whisper) and LLM extraction; the caller posts both as data.",
    "A live CRM connection. The CRM custom fields are staged as data and the caller reports the outcome.",
    "Bi-directional CRM field sync. Only the outbound half is built, because this repository holds no CRM subscription or webhook receiver.",
    "A MEDDICC score. Coverage is reported and no scale is invented.",
    "Gong or another meeting recorder, Gmail or Microsoft 365, calendar and telephony connectors.",
    "AI-generated reports and dashboards beyond the summary and coverage routes.",
)


def vocabulary() -> dict[str, Any]:
    """The vocabulary this workflow enforces, and every decision behind it.

    Served by the vocabulary route so the page reads its states from here rather
    than hard-coding them, and so a reviewer can read each decision without
    opening the source.
    """
    return {
        "source_kinds": list(SOURCE_KINDS),
        "default_source_kind": DEFAULT_SOURCE_KIND,
        "extraction_outputs": list(EXTRACTION_OUTPUTS),
        "meddicc": [
            {"key": key, "letter": letter, "label": label} for key, letter, label in MEDDICC_LETTERS
        ],
        "destination_kinds": list(DESTINATION_KINDS),
        "destination_labels": dict(DESTINATION_LABELS),
        "destination_states": list(DESTINATION_STATES),
        "crm_outcomes": list(CRM_OUTCOMES),
        "crm_success_outcomes": list(CRM_SUCCESS_OUTCOMES),
        "crm_not_sent": CRM_NOT_SENT,
        "default_crm_fields": dict(DEFAULT_CRM_FIELDS),
        "decisions": dict(DECISIONS),
        "not_implemented": list(NOT_IMPLEMENTED),
    }


# --------------------------------------------------------------------------- #
# Reading values
# --------------------------------------------------------------------------- #


def _parse_when(value: Any) -> datetime | None:
    """An ISO 8601 timestamp, or ``None`` when the value cannot be read."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _item_text(item: Any) -> str:
    """One next step or requirement as text, whatever shape it arrived in."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        for key in ("text", "action", "summary", "detail", "title"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(dict(item), sort_keys=True)
    return str(item)


def _read_items(value: Any, field: str, errors: dict[str, str]) -> list[Any]:
    """A list of next steps or requirements. Each item is text or a small map."""
    if value is None:
        return []
    if not isinstance(value, list):
        errors[field] = "Must be a list."
        return []
    for item in value:
        if not isinstance(item, (str, Mapping)):
            errors[field] = "Each item must be text or an object."
            return []
    return list(value)


def _evidence_lines(value: Any) -> list[str]:
    """The evidence lines for one MEDDICC letter, in whatever shape it arrived."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            lines.extend(_evidence_lines(item))
        return lines
    if isinstance(value, Mapping):
        text = _item_text(value)
        return [text] if text else []
    return [str(value)]


def _read_meddicc(value: Any, errors: dict[str, str]) -> dict[str, Any]:
    """The MEDDICC evidence, keyed by letter."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        errors["meddicc"] = "Must be an object keyed by MEDDICC letter."
        return {}
    unknown = sorted(str(key) for key in value if key not in MEDDICC_KEYS)
    if unknown:
        errors["meddicc"] = (
            f"Unknown MEDDICC key(s): {', '.join(unknown)}. Known keys: {', '.join(MEDDICC_KEYS)}."
        )
        return {}
    return {key: value[key] for key in MEDDICC_KEYS if key in value}


def _read_field_map(value: Any, errors: dict[str, str]) -> dict[str, str]:
    """A CRM custom-field override map: a logical name to an API field name."""
    if value is None:
        return dict(DEFAULT_CRM_FIELDS)
    if not isinstance(value, Mapping):
        errors["field_map"] = "Must be an object of logical name to CRM field name."
        return {}
    field_map = dict(DEFAULT_CRM_FIELDS)
    for key, target in value.items():
        if not isinstance(target, str) or not target.strip():
            errors["field_map"] = "Each CRM field name must be text."
            return {}
        field_map[str(key)] = target.strip()
    return field_map


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


def data_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The payload of a record, as a fresh mutable dict."""
    return dict(record.get("data") or {})


def present_destination(record: Mapping[str, Any]) -> dict[str, Any]:
    """One fan-out row, flattened."""
    return {"id": record["id"], "room_id": record.get("room_id"), **data_of(record)}


class ConversationIntelligenceEngine:
    """Reads and writes captures and their fan-out rows.

    ``now`` is injectable so a test can pin the clock. The production route leaves
    it at the real clock.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or _utcnow

    # -- reads -------------------------------------------------------------- #

    def _require(self, collection: str, record_id: str, label: str) -> dict[str, Any]:
        record = self.store.get(record_id)
        if record is None or record.get("collection") != collection:
            raise CaptureNotFound(f"No such {label}: {record_id}")
        return record

    def read_capture(self, capture_id: str) -> dict[str, Any]:
        record = self._require(CAPTURE_COLLECTION, capture_id, "capture")
        return self._present_capture(record)

    def read_destination(self, destination_id: str) -> dict[str, Any]:
        record = self._require(DESTINATION_COLLECTION, destination_id, "destination")
        return present_destination(record)

    def list_captures(
        self, room_id: str | None = None, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        if room_id:
            records = self.store.find(CAPTURE_COLLECTION, {ROOM_REF: room_id}, limit=limit)
        else:
            records = self.store.list(CAPTURE_COLLECTION, limit=limit)
        return [self._present_capture(record) for record in records]

    def destinations_of(self, capture_id: str) -> list[dict[str, Any]]:
        records = self.store.find(
            DESTINATION_COLLECTION, {CAPTURE_REF: capture_id}, limit=len(DESTINATION_KINDS) + 5
        )
        return [present_destination(record) for record in records]

    def coverage(self, room_id: str | None = None) -> dict[str, Any]:
        """MEDDICC coverage across the captures in scope, with no score."""
        captures = self.list_captures(room_id)
        counts = {key: 0 for key in MEDDICC_KEYS}
        for capture in captures:
            for key in covered_letters(capture.get("extraction")):
                counts[key] += 1
        letters = [
            {
                "key": key,
                "letter": letter,
                "label": label,
                "captures_with_evidence": counts[key],
                "covered": counts[key] > 0,
            }
            for key, letter, label in MEDDICC_LETTERS
        ]
        covered = [row["key"] for row in letters if row["covered"]]
        return {
            "room_id": room_id,
            "captures": len(captures),
            "letters": letters,
            "covered_letters": covered,
            "missing_letters": [key for key in MEDDICC_KEYS if key not in covered],
            "letters_total": len(MEDDICC_KEYS),
            "letters_with_evidence": len(covered),
            "note": (
                "Coverage counts a letter when at least one capture carries evidence for "
                "it. This build records evidence; it does not score it."
            ),
            "generated_at": self._now().isoformat(),
        }

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        captures = self.list_captures(room_id)
        destinations = self._destinations_in_scope(room_id)
        states = {kind: 0 for kind in DESTINATION_KINDS}
        written = failed = 0
        for destination in destinations:
            if destination.get("state") == FAILED:
                failed += 1
            else:
                written += 1
            kind = destination.get("kind")
            if kind in states:
                states[kind] += 1
        agreed = partial = plan_linked = 0
        for capture in captures:
            if capture["agreement"]["agreed"]:
                agreed += 1
            else:
                partial += 1
            if capture.get("plan_ref"):
                plan_linked += 1
        return {
            "room_id": room_id,
            "captures": len(captures),
            "complete_extractions": sum(1 for c in captures if _is_complete(c)),
            "destinations": len(destinations),
            "room_note": states[ROOM_NOTE],
            "handoff_packet": states[HANDOFF_PACKET],
            "crm_field": states[CRM_FIELD],
            "written": written,
            "failed": failed,
            "agreed": agreed,
            "partial": partial,
            "plan_linked": plan_linked,
            "letters_total": len(MEDDICC_KEYS),
            "generated_at": self._now().isoformat(),
        }

    # -- writes ------------------------------------------------------------- #

    def create_capture(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None,
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Record one conversation, fan it out, and verify it reached every destination.

        The extraction arrives as data (see the module docstring). The fan-out
        writes one row per destination kind, then reads them back: a fan-out that
        reached fewer destinations than the workflow requires raises
        :class:`CaptureRefused` rather than reporting a capture as complete.
        """
        if not str(room_id or "").strip():
            raise CaptureError("A capture belongs to a room.", {"room_id": "Name the room."})
        normalised = validate_capture(payload)
        extraction = {
            "summary": normalised["summary"],
            "next_steps": normalised["next_steps"],
            "requirements": normalised["requirements"],
            "meddicc": normalised["meddicc"],
        }
        occurred_at = normalised.get("occurred_at") or self._now().isoformat()
        capture = self.store.create(
            CAPTURE_COLLECTION,
            {
                ROOM_REF: room_id,
                "source_kind": normalised["source_kind"],
                "occurred_at": occurred_at,
                "transcript_ref": normalised.get("transcript_ref"),
                "plan_ref": normalised.get("plan_ref"),
                "extraction": extraction,
                "field_map": normalised["field_map"],
                "destination_ids": [],
            },
            room_id=room_id,
            source=source,
            actor=actor,
        )

        destination_ids: list[str] = []
        for kind in DESTINATION_KINDS:
            row = self.store.create(
                DESTINATION_COLLECTION,
                {
                    ROOM_REF: room_id,
                    CAPTURE_REF: capture["id"],
                    "kind": kind,
                    "label": DESTINATION_LABELS[kind],
                    "state": WRITTEN,
                    "payload": self._destination_payload(kind, extraction, normalised["field_map"]),
                    "reports": [],
                },
                room_id=room_id,
                source=source,
                actor=actor,
            )
            destination_ids.append(row["id"])

        self.store.update(
            capture["id"],
            {"destination_ids": destination_ids},
            source=source,
            actor=actor,
        )

        found = self.store.find(
            DESTINATION_COLLECTION, {CAPTURE_REF: capture["id"]}, limit=len(DESTINATION_KINDS) + 5
        )
        if len(found) != len(DESTINATION_KINDS):
            raise CaptureRefused(
                CaptureRefused.REASON_FANOUT_INCOMPLETE,
                f"The fan-out wrote {len(found)} of {len(DESTINATION_KINDS)} destinations.",
            )
        return self.read_capture(capture["id"])

    def report_destination(
        self,
        destination_id: str,
        payload: Mapping[str, Any] | None,
        *,
        source: str,
        actor: str,
    ) -> dict[str, Any]:
        """Report the outbound outcome of one fan-out row, then read it back.

        A CRM field reports a CRM outcome; the two local destinations report this
        workflow's own two states. The state is derived from the outcome, the
        report is appended to the row, and a write that does not read back raises
        :class:`CaptureRefused` rather than reporting success.
        """
        record = self._require(DESTINATION_COLLECTION, destination_id, "destination")
        kind = validate_destination_kind(str(data_of(record).get("kind") or ""))
        report = validate_report(kind, payload)
        outcome = report["outcome"]
        if kind == CRM_FIELD:
            state = WRITTEN if outcome in CRM_SUCCESS_OUTCOMES else FAILED
            patch = {"crm_state": outcome, "state": state}
        else:
            patch = {"state": outcome}
        history = list(data_of(record).get("reports") or [])
        history.append({"outcome": outcome, "note": report["note"], "at": self._now().isoformat()})
        self.store.update(
            destination_id,
            {**patch, "reports": history},
            source=source,
            actor=actor,
        )
        fresh = self.store.get(destination_id)
        if fresh is None or data_of(fresh).get("state") != patch["state"]:
            raise CaptureRefused(CaptureRefused.REASON_WRITE_NOT_VISIBLE)
        capture_id = str(data_of(fresh).get(CAPTURE_REF) or "")
        presented = present_destination(fresh)
        presented["capture_agreement"] = self._agreement_for_capture(capture_id)
        return presented

    # -- internals ---------------------------------------------------------- #

    def _destinations_in_scope(self, room_id: str | None) -> list[dict[str, Any]]:
        if room_id:
            records = self.store.find(DESTINATION_COLLECTION, {ROOM_REF: room_id}, limit=1000)
        else:
            records = self.store.list(DESTINATION_COLLECTION, limit=1000)
        return [present_destination(record) for record in records]

    def _agreement_for_capture(self, capture_id: str) -> dict[str, Any]:
        kind_states = {
            row["kind"]: row["state"] for row in self.destinations_of(capture_id) if row.get("kind")
        }
        return agreement_of(kind_states)

    def _present_capture(self, record: Mapping[str, Any]) -> dict[str, Any]:
        destinations = self.destinations_of(record["id"])
        kind_states = {row["kind"]: row["state"] for row in destinations if row.get("kind")}
        extraction = data_of(record).get("extraction") or {}
        covered = covered_letters(extraction)
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            **data_of(record),
            "destinations": destinations,
            "agreement": agreement_of(kind_states),
            "meddicc_coverage": {
                "covered_letters": covered,
                "missing_letters": [key for key in MEDDICC_KEYS if key not in covered],
                "letters_total": len(MEDDICC_KEYS),
            },
        }

    def _destination_payload(
        self,
        kind: str,
        extraction: Mapping[str, Any],
        field_map: Mapping[str, str],
    ) -> dict[str, Any]:
        """The body of one fan-out row, from the one extraction."""
        if kind == ROOM_NOTE:
            return {
                "title": f"Conversation summary ({extraction.get('summary', '')[:40]})",
                "body": extraction.get("summary", ""),
                "next_steps": list(extraction.get("next_steps", [])),
                "requirements": list(extraction.get("requirements", [])),
            }
        if kind == HANDOFF_PACKET:
            return {
                "summary": extraction.get("summary", ""),
                "requirements": list(extraction.get("requirements", [])),
                "next_steps": list(extraction.get("next_steps", [])),
                "meddicc": dict(extraction.get("meddicc") or {}),
                "note": "Post-sales reads the requirements here without re-listening to the calls.",
            }
        return {
            "fields": crm_field_values(extraction, field_map),
            "field_map": dict(field_map),
            "crm_state": CRM_NOT_SENT,
            "crm_outcome": None,
            "note": (
                "Staged here. This repository holds no live CRM connection, so the "
                "outbound outcome is reported to this row."
            ),
        }


def _is_complete(capture: Mapping[str, Any]) -> bool:
    """Whether a capture carries all four of the researched extraction outputs."""
    extraction = capture.get("extraction") or {}
    if not str(extraction.get("summary") or "").strip():
        return False
    if not extraction.get("next_steps"):
        return False
    if not extraction.get("requirements"):
        return False
    return bool(covered_letters(extraction))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
