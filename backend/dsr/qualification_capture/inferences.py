"""Every judgement call the research for WF-123 left open, named and served.

The specification names the four dimensions, one visibility rule and four
automations. It does not name an answer scale, a band cut, a routing target, or
how many of the four automations this plugin owns. This module records each of
those choices with its basis, so a reviewer can tell a sourced fact from a house
decision without reading the code.

Served by ``GET /api/wf-123/inferences`` so a panel cannot drift from the rules
the engine enforces.
"""

from __future__ import annotations

from typing import Any

#: Every inference, each with the question it answers, the options considered,
#: the choice made, and why. ``basis`` is ``"sourced"`` for a restatement of the
#: research and ``"inferred"`` for a decision this build made.
INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "answer_scale",
        "question": "What answer options and weights do the four dimensions use?",
        "options": ["a_free_text_field", "a_closed_ladder_per_dimension", "no_scale"],
        "choice": "a_closed_ladder_per_dimension",
        "basis": "inferred",
        "note": (
            "The research names budget, timeline, authority and procurement path "
            "and no scale. A closed ladder per dimension is what lets the score be "
            "a sum rather than a judgement, and what keeps a buyer answering in "
            "the room without a free-text box the seller has to read by hand."
        ),
    },
    {
        "id": "band_thresholds",
        "question": "Where does one qualification band end and the next begin?",
        "options": ["fixed_integer_cuts", "ratios_of_the_reachable_maximum"],
        "choice": "ratios_of_the_reachable_maximum",
        "basis": "inferred",
        "note": (
            "The research names a qualification band and no cut. Ratios travel "
            "with a form that enables fewer than four dimensions; a fixed integer "
            "would mark a perfect answer on a two-dimension form as unqualified."
        ),
    },
    {
        "id": "routing_targets",
        "question": "Where does each band route to?",
        "options": ["named_owner", "named_queue_by_band", "no_route"],
        "choice": "named_queue_by_band",
        "basis": "inferred",
        "note": (
            "The research says 'Auto-route on qualification band' and names no "
            "target. The plugin routes to a queue name per band and lets a form "
            "override it, rather than guessing a person."
        ),
    },
    {
        "id": "automation_ownership",
        "question": "How many of the four named automations does this plugin own?",
        "options": ["all_four", "the_two_that_are_in_room", "none"],
        "choice": "the_two_that_are_in_room",
        "basis": "inferred",
        "note": (
            "The research lists auto-route, auto-create-or-update a lead, "
            "auto-assign an owner and auto-trigger a follow-up task, and does not "
            "say how many belong here. This plugin owns attaching the submission to "
            "the deal and routing on the band. Creating a CRM object belongs to "
            "dsr.crm_upsert and the follow-up task belongs to WF-116; both are "
            "named in the routing decision, not opened here."
        ),
    },
    {
        "id": "anonymous_capture",
        "question": "May a submission arrive with no identity session?",
        "options": ["require_a_session", "allow_anonymous", "require_verification"],
        "choice": "allow_anonymous",
        "basis": "sourced",
        "note": (
            "The form renders inside the room, which is already behind WF-015's "
            "access gate, so the buyer need not re-prove an identity to answer. "
            "When a session is named it must resolve to WF-015's store; when it is "
            "not, the capture is recorded as anonymous rather than refused."
        ),
    },
    {
        "id": "buyer_visibility",
        "question": "Which fields may a buyer response carry?",
        "options": ["everything", "the_form_and_their_own_answers"],
        "choice": "the_form_and_their_own_answers",
        "basis": "sourced",
        "note": (
            "Quoted: 'qualification summary visible to seller only'. A buyer view "
            "carries the form and the buyer's own answers and strips every field "
            "named in the seller-only list."
        ),
    },
    {
        "id": "autosave_scope",
        "question": "What does an auto-save do to the qualification?",
        "options": ["score_the_partial_answer", "keep_the_draft_unscored"],
        "choice": "keep_the_draft_unscored",
        "basis": "inferred",
        "note": (
            "The research names 'auto-save of answers' and no scoring of a partial "
            "answer. A half-finished ladder is not a qualification, so a draft stays "
            "unscored until it is submitted."
        ),
    },
    {
        "id": "room_state_on_publish",
        "question": "May a form go live on a room that is not active?",
        "options": ["any_room_state", "active_rooms_only"],
        "choice": "active_rooms_only",
        "basis": "sourced",
        "note": (
            "Reuses dsr.room_lifecycle: an archived room is closed to buyers, so a "
            "live form on it would be a buyer surface nobody can reach. Publication "
            "is refused and the room's state is named in the refusal."
        ),
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One inference by its id, or ``None``."""

    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe_inferences() -> list[dict[str, Any]]:
    """Every inference as plain data, for the route and the page."""

    return [dict(entry) for entry in INFERENCES]


__all__ = ["INFERENCES", "by_id", "describe_inferences"]
