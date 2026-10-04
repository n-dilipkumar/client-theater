"""Every judgement call this workflow made, named and served over HTTP.

The research for WF-060 is explicit that it does not state a flow: "This spec
does not state the flow as separate fields. The evidence below is the
specification. An implementer who needs a flow the evidence does not contain must
derive it and record the derivation, not assume it."

So the derivations are recorded here as data, each with the evidence it rests on
and the reading it rejected. The frontend serves them, so a reviewer can read
what the workflow decided without reading the Python, and ``GET /wf-060/decisions``
returns them for the same reason.

Every entry is a decision this package made. None of them is a restatement of the
research: a decision the research states is not a decision call, it is a
requirement, and requirements live in the vocabulary.
"""

from __future__ import annotations

from typing import Any

#: Each entry: what was decided, the evidence it rests on, what was rejected, and
#: what the rejected reading would have cost.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "wf060-enforcement-off-makes-the-page-advisory",
        "question": ("What does the consent gate do when 'Enforce use of consent page' is off?"),
        "decision": (
            "The consent page is still issued and a decision is still recorded, and the "
            "recording proceeds whatever the decision was."
        ),
        "evidence": (
            "The research states what enforcement achieves: 'Check to ensure Gong only "
            "records meetings where consent was explicitly given via the consent page to "
            "record the call.' It says nothing about the switch being clear, and names it "
            "as an open point for the implementer."
        ),
        "rejected": (
            "Refuse to record whenever the page is off or enforcement is off. That makes "
            "the switch a description of the product rather than a choice by the "
            "administrator."
        ),
        "cost_of_the_rejected_reading": (
            "Clearing one checkbox would silently stop every recording in the organisation "
            "while every booking still succeeded, and nothing in the product would say so."
        ),
        "residual_risk": (
            "With enforcement off and a decline recorded, the call is recorded after a "
            "participant said no. The record carries consent_state 'declined' with "
            "enforced false, and the page says so, so the situation is visible to a "
            "reviewer rather than buried."
        ),
        "surface": "decisions.initial / decisions.advance",
    },
    {
        "id": "wf060-consent-is-required-only-when-page-and-enforcement-are-both-on",
        "question": "On which profiles is anybody asked for consent at all?",
        "decision": (
            "Only when the consent page is on and enforcement is on. Every other profile "
            "starts its bookings in state 'scheduled' with the recording armed."
        ),
        "evidence": (
            "The same sentence as above, plus Gong's documented 409, 'Conflict, e.g. "
            "consent page is not enabled in your company', which shows the page has to be "
            "on for a consent meeting to exist."
        ),
        "rejected": (
            "Ask for consent whenever the page is on, regardless of enforcement. That "
            "would collect decisions the product is about to ignore."
        ),
        "cost_of_the_rejected_reading": (
            "Participants would be asked to agree to a recording whose outcome their answer "
            "does not change, which is a consent notice that consents to nothing."
        ),
        "residual_risk": (
            "An administrator who reads 'consent page on' as 'we ask consent' will not get "
            "a question until they also tick enforcement. The page states the current "
            "requirement in words rather than leaving it to be inferred."
        ),
        "surface": "decisions.consent_required",
    },
    {
        "id": "wf060-join-without-consent-cancels-only-under-enforcement",
        "question": "What happens when a participant joins without consenting?",
        "decision": (
            "The join is admitted only where the profile allows it, and the recording is "
            "cancelled. Under a profile that does not allow it, the participant is refused."
        ),
        "evidence": (
            "Step 2: 'Optionally enables Allow participants to join without giving consent "
            "(recording will be canceled)'. The parenthetical is the researched "
            "consequence, and it is attached to the switch itself."
        ),
        "rejected": (
            "Admit the join and keep recording. That would make the switch a description "
            "of the call rather than a decision about the recording."
        ),
        "cost_of_the_rejected_reading": (
            "The switch would have no effect at all, and an administrator who enabled it "
            "believing the recording would stop would be wrong."
        ),
        "residual_risk": (
            "A booking that requires consent but does not allow the join has one path for a "
            "participant who does not answer: they are refused. That is the safe direction, "
            "and it is why the default for the switch is closed."
        ),
        "surface": "decisions.advance / errors.JoinWithoutConsentRefused",
    },
    {
        "id": "wf060-a-decline-cancels-the-recording-only-under-enforcement",
        "question": "Does a decline cancel the recording?",
        "decision": (
            "Under enforcement, yes. Without enforcement, no: the decline is stored as "
            "evidence and the recording is armed."
        ),
        "evidence": (
            "The automations: 'consent page enforcement cancels recording when declined'. "
            "The word that does the work is enforcement."
        ),
        "rejected": "Cancel the recording on a decline whatever the enforcement setting.",
        "cost_of_the_rejected_reading": (
            "Enforcement off would still produce a booking whose recording state is "
            "cancelled, so the setting would control nothing and the record would "
            "contradict the administrator's configuration."
        ),
        "residual_risk": (
            "None beyond the one already recorded for enforcement off, which is the same "
            "situation seen from the other side."
        ),
        "surface": "decisions.advance",
    },
    {
        "id": "wf060-audio-prompt-fires-once-per-call",
        "question": "When does the audio prompt fire?",
        "decision": (
            "On the first guest with audio on. 'every_guest' remains accepted by the "
            "validator and can be stored on a profile."
        ),
        "evidence": (
            "The research offers both: 'audio prompt fires on first guest with audio on "
            "(or on every guest)'. It chooses neither."
        ),
        "rejected": "Fire on every guest.",
        "cost_of_the_rejected_reading": (
            "The prompt is a disclosure to the room, and a disclosure heard once has been "
            "made. Replaying it per participant turns a legal notice into an argument that "
            "continues after the person joining has already been told."
        ),
        "residual_risk": (
            "A late joiner does not hear the prompt. That is why the profile's consent page "
            "and its pre-call email carry the same disclosure, and why both exist."
        ),
        "surface": "profiles.validate_audio_prompt",
    },
    {
        "id": "wf060-a-stale-link-is-disabled-not-deleted",
        "question": "How is 'the previous link is disabled' represented?",
        "decision": (
            "As link_state 'superseded' on the record, with superseded_at and "
            "superseded_by stamped onto it. Never a delete."
        ),
        "evidence": (
            "The automations: 'Each time you change this link, the previous link is "
            "disabled.' The brief requires it as a state so the audit row survives."
        ),
        "rejected": "Delete the previous link record.",
        "cost_of_the_rejected_reading": (
            "The audit log would point at a record that no longer exists, and 'which link "
            "did the buyer actually join' would be unanswerable for exactly the bookings "
            "where it matters, which is a booking whose link was changed."
        ),
        "residual_risk": (
            "Retiring an already-superseded link is refused, so a second change cannot "
            "overwrite the first superseded_at."
        ),
        "surface": "links.retire_previous",
    },
    {
        "id": "wf060-the-profile-resolves-by-organiser-email",
        "question": "Which key does the consent profile resolve on?",
        "decision": (
            "The organiser's email, through the user directory. Never the room, never the booking."
        ),
        "evidence": (
            "organizerEmail is documented as 'The email address of the user creating the "
            "meeting, the Gong consent page link will be used according to the settings of "
            "this user.' The brief asks for the key to be recorded."
        ),
        "rejected": "Resolve the profile from the room the booking belongs to.",
        "cost_of_the_rejected_reading": (
            "A shared booking page can be booked by any rep, so a room-scoped lookup would "
            "apply one seller's consent settings to another seller's meeting. That is a "
            "compliance defect, not a cosmetic one."
        ),
        "residual_risk": (
            "An organiser with no directory entry has no profile and the call is refused "
            "rather than given an organisation default, which is Gong's documented 404 "
            "behaviour."
        ),
        "surface": "directory.resolve / vocabulary.PROFILE_RESOLUTION_KEY",
    },
    {
        "id": "wf060-one-record-per-booking",
        "question": "Which record shape carries the consent decision and the recording outcome?",
        "decision": (
            "One record per booking, with a single state naming the current point in the "
            "machine and consent_state and recording_state carrying the two axes."
        ),
        "evidence": (
            "Jev chose this shape over two collections and over an append-only event log, "
            "audit jev-20261004T064834-23344-14922, confidence 0.80."
        ),
        "rejected": (
            "Two collections joined at read time, and an append-only event log folded at read time."
        ),
        "cost_of_the_rejected_reading": (
            "A booking produces a handful of rows rather than a stream, and both a seller "
            "and a participant read the state on a page. A join or a fold on the read path "
            "puts that reconstruction in front of every page load for no gain."
        ),
        "residual_risk": (
            "The two axes are stored separately so they can be queried, which is separate "
            "storage and therefore separate truth. decisions.is_consistent checks the two "
            "agree, and a record whose axes contradict its state is refused."
        ),
        "surface": "engine.open / decisions.is_consistent",
    },
    {
        "id": "wf060-the-vendor-request-is-recorded-not-sent",
        "question": "Does this workflow call Gong's Meetings API?",
        "decision": (
            "No socket is opened. The exact NewMeetingRequest the research quotes is built "
            "and stored on the record, and the NewMeetingResponse is read through a "
            "function that turns both documented failures into refusals."
        ),
        "evidence": (
            "The source is 'Meetings (in Beta Phase)', and the request and response shapes "
            "are therefore treated as unversioned."
        ),
        "rejected": "Call the endpoint from the request handler.",
        "cost_of_the_rejected_reading": (
            "A beta endpoint would be on the page of every booking, a network timeout would "
            "be an HTTP failure a seller cannot act on, and the recorded request could not "
            "be compared against the research."
        ),
        "residual_risk": (
            "Nothing here proves the real endpoint agrees with the research. What it does "
            "is make the disagreement visible: the outbound request is stored on the record "
            "a reviewer can read, and the two documented failures are raised rather than "
            "turned into a link that silently never joins."
        ),
        "surface": "links.new_meeting_request / links.new_meeting_response",
    },
    {
        "id": "wf060-an-invitee-may-block-its-own-recording",
        "question": "Whose recording settings decide whether a call can be recorded?",
        "decision": (
            "The organiser's record-by-Gong flag, plus a separate check of every invitee "
            "for the researched flag 'if the invitation of this user to a web conference "
            "will prevent its recording'."
        ),
        "evidence": (
            "The data sources name 'Gong user directory + per-user settings (import "
            'emails, record-by-Gong flag, "if the invitation of this user to a web '
            "conference will prevent its recording\")'."
        ),
        "rejected": (
            "Check only the organiser's flag, on the grounds that the other two settings "
            "are about the seller."
        ),
        "cost_of_the_rejected_reading": (
            "A booking could be issued and its recording called off by the participant, "
            "after the invite went out. The caller would learn of it from a missing "
            "recording rather than from the booking."
        ),
        "residual_risk": (
            "The organiser's domain is the heuristic for 'external invitee', because the "
            "research says 'external invitees' without defining external. A company with "
            "two domains has to say which is the company."
        ),
        "surface": "directory.can_record / directory.recording_blocked_by_invitee",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One inference by its id, or ``None``."""
    for inference in INFERENCES:
        if inference["id"] == inference_id:
            return dict(inference)
    return None


def describe() -> list[dict[str, Any]]:
    """Every inference, as the frontend serves it."""
    return [dict(inference) for inference in INFERENCES]


def describe_one(inference_id: str) -> dict[str, Any]:
    """One inference as the frontend serves it, or a 404-shaped refusal.

    Raises rather than returning ``None``, because the only caller is a route
    that must answer 404 for an id nobody wrote.
    """
    found = by_id(inference_id)
    if found is None:
        from dsr.recording_consent.errors import ProfileInvalid

        raise ProfileInvalid(
            f"No inference named {inference_id!r}.",
            {"inference_id": "is not one this workflow recorded"},
        )
    return found
