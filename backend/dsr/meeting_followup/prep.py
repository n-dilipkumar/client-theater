"""The pre-meeting prep page, the dry run, and Ask anything.

The research describes the prep page as an "AI meeting prep page" with "pre-meeting
summary (objectives, key topics, prior discussion highlights, Action items for you,
suggested questions, reminders)", a "participant list with drill-down activity
timelines", "Ask anything", and a "recent-activity feed (calls, emails, digital
interactions, CRM changes, texts)".

It names no summarisation model, so this build assembles the page from the
records already in the store and says so. A summary invented by a model with no
named source would be a claim the evidence does not support; an assembly from
stored facts is checkable.

Three derived behaviours are recorded rather than assumed, and each is served so a
page can display it:

**Dry run is derived.** The research says the rep "rehearses with an AI participant
built from the meeting context, chooses a voice, gets feedback, and iterates with
'Go again'". No provider, no voice catalogue and no synthesis is named, so the
participant is a persona built from the meeting context, the voices are labels
this build accepts, and the feedback is a checklist over the prep sections. The
dry run is stored and auditable; it is not a call to any model.

**Ask anything answers from the prep context.** The research gives the feature a
name and no behaviour at all. This build records the question and answers it from
the prep sections, and every answer names the sections it drew on. It does not
pretend to be a retrieval system over documents the evidence never mentions.

**The mobile app omits the recent-activity feed.** Quoted: "The meeting prep page
appears slightly differently in the mobile app and does not display recent
activities." :func:`page_view` honours that, so a mobile client asking for the
same page is answered without the feed rather than with an empty one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from dsr.meeting_followup import vocabulary as vocab
from dsr.meeting_followup.errors import PrepPageInvalid

# --------------------------------------------------------------------------- #
# The prep page
# --------------------------------------------------------------------------- #


def normalise_page(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a prep page payload into a normalised dict.

    The six researched sections are keys this build recognises, and any other key
    is kept: the record payload is ordinary JSON, so a team adding a section needs
    no migration and no coordination.
    """
    meeting_id = str(payload.get("meeting_id") or payload.get("meetingId") or "").strip()
    if not meeting_id:
        raise PrepPageInvalid("meeting_id is required", {"meeting_id": "required"})

    start = str(payload.get("starts_at") or "").strip()
    if start and _readable(start) is None:
        raise PrepPageInvalid(
            "starts_at must be an ISO 8601 timestamp",
            {"starts_at": "ISO 8601"},
        )

    summary = dict(payload.get("summary") or {})
    known = {key: summary[key] for key in vocab.PREP_SECTIONS if key in summary}
    extra = {key: value for key, value in summary.items() if key not in vocab.PREP_SECTIONS}

    participants = payload.get("participants") or []
    if not isinstance(participants, list):
        raise PrepPageInvalid(
            "participants must be a list",
            {"participants": "a list of participant records"},
        )

    return {
        "meeting_id": meeting_id,
        "title": str(payload.get("title") or "").strip(),
        "starts_at": start,
        "objective": str(payload.get("objective") or "").strip(),
        "summary": known,
        "summary_extra": extra,
        "participants": [dict(item) for item in participants],
        "summary_source": str(payload.get("summary_source") or "assembled_from_store"),
        "entry_point": str(payload.get("entry_point") or "").strip(),
    }


def _readable(raw: str) -> datetime | None:
    """Whether a timestamp can be read, and as what."""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def page_view(
    page: Mapping[str, Any],
    *,
    activities: list[Mapping[str, Any]] | None = None,
    mobile: bool = False,
) -> dict[str, Any]:
    """The prep page as a rep reads it.

    ``mobile`` drops the recent-activity feed, which the research says the mobile
    app does. The flag travels with the request rather than being inferred, because
    a server that guessed would silently withhold a section a desktop rep needed.
    """
    activities = list(activities or [])
    body: dict[str, Any] = {
        "meeting_id": page.get("meeting_id"),
        "title": page.get("title"),
        "starts_at": page.get("starts_at"),
        "objective": page.get("objective"),
        "summary": dict(page.get("summary") or {}),
        "summary_extra": dict(page.get("summary_extra") or {}),
        "sections": list(vocab.PREP_SECTIONS),
        "summary_source": page.get("summary_source"),
        "participants": [
            {**dict(participant), "has_activity_timeline": bool(participant.get("activity"))}
            for participant in page.get("participants") or []
        ],
        "entry_points": list(vocab.PREP_ENTRY_POINTS),
        "keyboard_shortcut": vocab.PREP_KEYBOARD_SHORTCUT,
        "mobile": bool(mobile),
    }
    if mobile:
        body["recent_activity"] = None
        body["recent_activity_omitted_because"] = vocab.MOBILE_APP_OMITS_RECENT_ACTIVITY
    else:
        body["recent_activity"] = group_activities(activities)
        body["recent_activity_kinds"] = list(vocab.RECENT_ACTIVITY_KINDS)
    return body


def group_activities(activities: Iterable[Mapping[str, Any]]) -> dict[str, list[dict]]:
    """The recent-activity feed, grouped by the five researched kinds.

    Quoted: "a recent-activity feed (calls, emails, digital interactions, CRM
    changes, texts)". The five keys are always present, empty or not, so a client
    does not have to know a kind before it can render the group.
    """
    grouped: dict[str, list[dict]] = {kind: [] for kind in vocab.RECENT_ACTIVITY_KINDS}
    for item in activities:
        kind = str(item.get("kind") or "")
        if kind in grouped:
            grouped[kind].append(dict(item))
    return grouped


# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #


def normalise_dry_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a dry-run request.

    The voice is checked against :data:`~dsr.meeting_followup.vocabulary.DRY_RUN_VOICES`
    because the research says the rep "chooses a voice" and a form that offers a
    voice the rules reject is a form that submits something unusable.
    """
    meeting_id = str(payload.get("meeting_id") or payload.get("meetingId") or "").strip()
    if not meeting_id:
        raise PrepPageInvalid("meeting_id is required", {"meeting_id": "required"})

    voice = str(payload.get("voice") or vocab.DRY_RUN_VOICES[0]).strip().lower()
    if voice not in vocab.DRY_RUN_VOICES:
        raise PrepPageInvalid(
            f"voice {voice!r} is not one this build offers",
            {"voice": f"one of: {', '.join(vocab.DRY_RUN_VOICES)}"},
        )

    return {
        "meeting_id": meeting_id,
        "voice": voice,
        "attempt": int(payload.get("attempt") or 1),
        "derived": vocab.DRY_RUN_IS_DERIVED,
        "note": (
            "No synthesis provider is named in the research. This is a derived "
            "rehearsal record, not a call to a model."
        ),
    }


def dry_run_participant(page: Mapping[str, Any], voice: str) -> dict[str, Any]:
    """The AI participant a dry run rehearses against.

    Derived from the meeting context, which is what the research names: "an AI
    participant built from the meeting context". Built from the stored sections
    rather than generated, so the persona cannot claim something the record does
    not support.
    """
    summary = dict(page.get("summary") or {})
    objectives = list(summary.get("objectives") or [])
    topics = list(summary.get("key_topics") or [])
    return {
        "built_from": "meeting_context",
        "voice": voice,
        "knows_objectives": objectives,
        "knows_topics": topics,
        "questions_will_probe": list(summary.get("suggested_questions") or []),
        "derived": True,
    }


def dry_run_feedback(
    page: Mapping[str, Any], covered: Iterable[str] | None = None
) -> dict[str, Any]:
    """The feedback a rehearsal returns.

    A checklist over the researched sections, not a score. The research says the
    rep "gets feedback" and describes none of it, so the three derived fields are
    served with the vocabulary so a reviewer sees that they are derived.

    ``covered`` is what the rehearsal actually touched. Left out, nothing is
    covered and every objective comes back open, which is the honest answer for a
    rehearsal that reported nothing.
    """
    summary = dict(page.get("summary") or {})
    covered = {str(item) for item in (covered or ())}
    return {
        "objectives_covered": [
            item for item in (summary.get("objectives") or []) if item in covered
        ],
        "objectives_total": len(summary.get("objectives") or []),
        "suggested_questions_used": [
            item for item in (summary.get("suggested_questions") or []) if item in covered
        ],
        "open_items": [
            item for item in (summary.get("action_items_for_you") or []) if item not in covered
        ],
        "fields": list(vocab.DRY_RUN_FEEDBACK_FIELDS),
        "again_label": vocab.DRY_RUN_AGAIN_LABEL,
        "derived": True,
    }


# --------------------------------------------------------------------------- #
# Ask anything
# --------------------------------------------------------------------------- #


def answer(page: Mapping[str, Any], question: str) -> dict[str, Any]:
    """Answer a question from the prep context.

    The research names "Ask anything" and nothing else, so this returns the
    sections that bear on the question and says the answer came from them. It does
    not invent a retrieval model: every answer names its sources and a question
    that no section bears on returns the list of sections rather than a guess.
    A section matches on two things, and both are deliberate. A question that
    *names* the section matches it: "what were the objectives?" is a question about
    ``objectives``, and the word appears nowhere in the objectives' own text. And a
    question that shares a word with the section's *content* matches it: "who signs
    off?" bears on ``action_items_for_you`` because the questionnaire is one of
    them. Matching on content alone answered the first question with nothing, which
    is the wrong answer rather than an honest empty one.
    """
    text = str(question or "").strip()
    summary = dict(page.get("summary") or {})
    tokens = _tokens(text)

    matched = [
        section
        for section in vocab.ASK_ANYTHING_ANSWER_SOURCES
        if section in tokens or _mentions(summary.get(section), tokens)
    ]
    return {
        "question": text,
        "sources": matched,
        "available_sections": list(vocab.ASK_ANYTHING_ANSWER_SOURCES),
        "answer": {section: summary.get(section) for section in matched},
        "derived": True,
        "matched_on": "section name or shared content word",
        "note": (
            "The research names Ask anything and no behaviour. This answers from "
            "the prep context and names every section it used."
        ),
    }


def _tokens(text: str) -> set[str]:
    """The words a question is made of, punctuation removed.

    Punctuation is stripped before the length test rather than after it. The
    difference is not cosmetic: without it "objectives?" is one token and does not
    match the section ``objectives``, so the most ordinary question a rep can ask
    about their own meeting returns nothing.
    """
    cleaned = "".join(character if character.isalnum() else " " for character in text.lower())
    return {word for word in cleaned.split() if len(word) > 3}


def _mentions(value: Any, tokens: set[str]) -> bool:
    """Whether a section's content shares a word with the question."""
    if not tokens:
        return False
    if isinstance(value, str):
        return bool(tokens & _tokens(value))
    if isinstance(value, list):
        return any(_mentions(item, tokens) for item in value)
    return False


# --------------------------------------------------------------------------- #
# The push reminder
# --------------------------------------------------------------------------- #


def push_reminder_due(starts_at: str, now: datetime) -> bool:
    """Whether the mobile push window is open.

    Quoted: push notifications fire "30 minutes before each external meeting".
    The window is the 30 minutes before the start, and the moment the start passes
    the window closes: a push that arrives after the call is noise.

    ``now`` is a parameter rather than a clock read, so a test freezes it. The
    engine holds the clock and passes it here.
    """
    start = _readable(str(starts_at or ""))
    if start is None:
        return False
    from datetime import timedelta

    window = start - timedelta(minutes=vocab.PUSH_NOTIFICATION_MINUTES)
    return window <= now <= start
