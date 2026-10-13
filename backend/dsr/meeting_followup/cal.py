"""Cal: the follow-up webhooks, the recording downloads, and the afterEvent workflow.

The issue requires each capability to be attributed to a vendor. Cal supplies the
follow-up webhooks and the recording download links; Gong supplies everything else
in this ticket.

Quoted webhook events:

    MEETING_ENDED, RECORDING_READY, RECORDING_TRANSCRIPTION_GENERATED,
    BOOKING_NO_SHOW_UPDATED

Quoted payloads:

    RECORDING_READY payload includes "downloadLink":
    "https://app.cal.com/api/video/recording?token=..."

    RECORDING_TRANSCRIPTION_GENERATED includes downloadLinks.transcription[] in
    json/srt/txt/vtt

Quoted workflow shape:

    POST /v2/workflows with trigger afterEvent (offset) and template completed /
    rating

Nothing here opens a socket or fetches a download link. A link whose token has
expired is exactly the thing a third party cannot repair, so the link is recorded
and its format is validated rather than fetched.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.meeting_followup import flows, vocabulary as vocab
from dsr.meeting_followup.errors import WebhookInvalid


def validate_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one inbound Cal webhook into a normalised dict.

    ``event`` is required and must be one of the four researched names. A webhook
    for an event this workflow does not model is refused rather than stored, so an
    operator reading the webhook log is not shown rows that drove nothing.
    """
    event = str(payload.get("event") or payload.get("eventType") or "").strip().upper()
    if not event:
        raise WebhookInvalid(
            "event is required",
            {"event": f"one of: {', '.join(vocab.CAL_WEBHOOK_EVENTS)}"},
        )
    if event not in vocab.CAL_WEBHOOK_EVENTS:
        raise WebhookInvalid(
            f"event {event!r} is not one of the researched Cal webhooks",
            {"event": f"one of: {', '.join(vocab.CAL_WEBHOOK_EVENTS)}"},
        )

    nested = payload.get("payload")
    booking = nested if isinstance(nested, Mapping) else payload
    booking_uid = str(booking.get("bookingUid") or booking.get("booking_uid") or "").strip()
    if not booking_uid:
        raise WebhookInvalid("bookingUid is required", {"bookingUid": "required"})

    out: dict[str, Any] = {
        "event": event,
        "booking_uid": booking_uid,
        "received_at": None,
        "cal_api_version": str(payload.get("cal-api-version") or "").strip(),
    }
    if event == vocab.WEBHOOK_RECORDING_READY:
        out["recording"] = recording_payload(booking)
    if event == vocab.WEBHOOK_TRANSCRIPTION_GENERATED:
        out["transcription"] = transcription_payload(booking)
    if event == vocab.WEBHOOK_NO_SHOW_UPDATED:
        out["no_show"] = str(booking.get("noShow") or booking.get("no_show") or "").strip()
    return out


def recording_payload(booking: Mapping[str, Any]) -> dict[str, Any]:
    """The ``RECORDING_READY`` payload, read as the research describes it.

    Quoted: "RECORDING_READY payload includes 'downloadLink': 'https://app.cal.com/
    api/video/recording?token=...'".
    """
    video = booking.get("video")
    video = video if isinstance(video, Mapping) else {}
    link = str(
        booking.get(vocab.RECORDING_DOWNLOAD_LINK_FIELD)
        or video.get(vocab.RECORDING_DOWNLOAD_LINK_FIELD)
        or ""
    ).strip()
    return {
        vocab.RECORDING_DOWNLOAD_LINK_FIELD: link,
        "has_link": bool(link),
        "fetched": False,
        "note": (
            "The link is recorded, not fetched. A recording link carries a token "
            "that expires, so a stored link is evidence that a recording existed."
        ),
    }


def transcription_payload(booking: Mapping[str, Any]) -> dict[str, Any]:
    """The ``RECORDING_TRANSCRIPTION_GENERATED`` payload.

    Quoted: "includes downloadLinks.transcription[] in json/srt/txt/vtt". The four
    formats are the researched set and nothing else is stored as a link, because a
    transcript a caller cannot parse is not a transcript. A format outside the set
    is reported rather than dropped.
    """
    raw = booking.get(vocab.TRANSCRIPTION_DOWNLOAD_LINKS_FIELD)
    raw = raw if isinstance(raw, Mapping) else {}
    entries = raw.get(vocab.TRANSCRIPTION_FORMAT_KEY)

    links: dict[str, str] = {}
    formats_seen: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        fmt = str(entry.get("format") or "").strip().lower()
        if not fmt:
            continue
        formats_seen.append(fmt)
        if fmt in vocab.TRANSCRIPTION_FORMATS:
            links[fmt] = str(entry.get("link") or entry.get("downloadLink") or "").strip()

    unknown = sorted({fmt for fmt in formats_seen if fmt not in vocab.TRANSCRIPTION_FORMATS})
    return {
        "formats": list(vocab.TRANSCRIPTION_FORMATS),
        "links": links,
        "available": sorted(links),
        "unknown_formats": unknown,
        "fetched": False,
        "note": (
            "Quoted: the transcription links ship in json, srt, txt and vtt. "
            "Anything outside that set is reported rather than stored as a link."
        ),
    }


def after_event_workflow(
    flow: Mapping[str, Any],
    *,
    booking_uid: str,
    event_ended_at: str = "",
) -> dict[str, Any]:
    """The ``POST /v2/workflows`` body a Cal afterEvent workflow would carry.

    Quoted: "POST /v2/workflows with trigger afterEvent (offset) and template
    completed / rating", and the issue adds "Cal afterEvent workflows with delay
    steps and conditional paths/filter gates".

    The offset is measured from the meeting **end**, which is why ``event_ended_at``
    travels with it: a follow-up anchored on the meeting start and one anchored on
    the meeting end are different sends, and only this route knows which one the
    seller's offset meant.
    """
    actions: list[dict[str, Any]] = []
    for step in flow.get("steps") or []:
        offset = int(step.get("offset_days") or 0)
        entry: dict[str, Any] = {
            "name": str(step.get("subject") or f"Touch {offset}"),
            "offset": {
                "time": offset,
                "unit": str(step.get("offset_unit") or vocab.DEFAULT_OFFSET_UNIT),
            },
            "action": "event_type",
        }
        if step.get("kind") == vocab.TODO_KINDS[1]:
            entry["eventType"] = str(step.get("template") or vocab.CAL_TEMPLATES[0])
        conditions = step.get("conditions") or []
        if conditions:
            entry["filter"] = {str(c.get("field") or ""): c.get("value") for c in conditions}
        actions.append(entry)

    return {
        "endpoint": vocab.CAL_WORKFLOW_ENDPOINT,
        "cal-api-version": "2024-08-13",
        "flowId": flows.new_flow_instance_id(str(flow.get("flow_id") or ""), booking_uid),
        "trigger": {
            "type": vocab.CAL_TRIGGER_AFTER_EVENT,
            "event": "meeting_ended",
            "at": event_ended_at or None,
        },
        "actions": actions,
        "templates": list(vocab.CAL_TEMPLATES),
        "sent": False,
        "note": (
            "This product is not a Cal client. The workflow is recorded so the "
            "researched call is reviewable, and it is not sent."
        ),
    }


def validate_workflow(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check an inbound Cal workflow against the published enums.

    A workflow this product cannot run is not an error: it is a workflow for Cal,
    and Cal is where it belongs. So the answer lists what would not survive, and a
    workflow with no actions is reported rather than passed as a valid no-op.
    """
    problems: list[dict[str, str]] = []
    notes: list[str] = []

    trigger = payload.get("trigger") or {}
    if str(trigger.get("type") or "") != vocab.CAL_TRIGGER_AFTER_EVENT:
        problems.append(
            {
                "field": "trigger.type",
                "problem": f"only {vocab.CAL_TRIGGER_AFTER_EVENT} is modelled",
            }
        )
    for action in payload.get("actions") or []:
        if not isinstance(action, Mapping):
            problems.append({"field": "actions", "problem": "each action must be an object"})
            continue
        template = str(action.get("eventType") or "").strip()
        if template and template not in vocab.CAL_TEMPLATES:
            problems.append(
                {
                    "field": "actions.eventType",
                    "problem": f"{template!r} is not one of {', '.join(vocab.CAL_TEMPLATES)}",
                }
            )
    if not payload.get("actions"):
        notes.append("A Cal workflow with no actions does nothing.")

    return {
        "valid": not problems,
        "problems": problems,
        "notes": notes,
        "templates": list(vocab.CAL_TEMPLATES),
        "events": list(vocab.CAL_WEBHOOK_EVENTS),
    }
