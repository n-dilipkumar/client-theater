"""The derived consent and recording state machine.

Why this module exists
----------------------

The researched specification at
``docs/research/digital-sales-room-workflows/wf/WF-060.md`` names the pieces of
this machine and never joins them. Its step 2 offers "Allow participants to join
without giving consent (recording will be canceled)", its automations say
"consent page enforcement cancels recording when declined", and its open points
say in the implementer's own words: "The two states are join-without-consent and
cancel-recording. Derive the state machine and record the derivation."

So the machine below is a derivation, not a quote. Everything it decides is
stated here with the evidence it rests on, and the readings it rejects are named
rather than dropped.

The derivation
--------------

Three inputs decide the machine: whether the consent page is on, whether
enforcement is on, and what the participant did.

**Enforcement off makes the consent page advisory.** This is the open point the
research does not answer: "Decide what the gate does when enforcement is off. The
evidence does not say."

The research states what enforcement *achieves*: "Check to ensure Gong only
records meetings where consent was explicitly given via the consent page to
record the call." So enforcement is the thing that makes a decision binding. With
it clear, the page is still issued and a decision is still recorded, because the
compliance evidence is worth keeping, and the recording proceeds regardless.

Rejected reading: with enforcement off, refuse to record. That would make the
switch a description of the product rather than a choice by the administrator, and
it would mean clearing one checkbox silently stops every recording in the
organisation while every booking still succeeds. The cost of the reading that was
chosen is stated plainly instead: with enforcement off and a decline recorded, the
call *is* recorded after a participant said no. The record carries
``consent_state="declined"`` and ``enforced=false`` and the page says so, so the
situation is visible to a reviewer rather than buried. An administrator who does
not want that outcome has one checkbox.

**Consent is required only when the page is on *and* enforcement is on.** The
same evidence: with the page off there is nothing for a participant to answer, and
Gong documents the consequence as a 409, which this workflow refuses before
issuing the link rather than after the call fails.

**A decline cancels the recording only under enforcement.** The automations say
"consent page enforcement cancels recording when declined", and the parenthetical
in step 2 attaches the same consequence to joining without consent. Both are
therefore conditional on the same switch.

**Joining without consent is only reachable where the profile allows it.** Step 2
offers "Allow participants to join without giving consent (recording will be
canceled)". With it off, a participant who arrives without consenting is refused,
which is the only reading under which the switch has an effect at all. That
refusal is :class:`~dsr.recording_consent.errors.JoinWithoutConsentRefused`.

**The two axes are stored separately.** ``state`` names the current point in the
machine, which is what a person reads. ``consent_state`` and ``recording_state``
name the two axes the machine actually moves on, which is what a query filters
on. :func:`~dsr.recording_consent.vocabulary.STATES` is the closed set of names.

Why the recording bot is not a state
------------------------------------

Step 7 says "the recording bot joins automatically", and the recording run does
not wait for a consent decision to start when the recording is already
``blocked``: a blocked recording is *cancelled* or *released*, never started. The
bot's presence is therefore recorded on the booking's invite, and the recording
axis moves to ``in_progress`` only from an ``armed`` state. That is what makes
"the call was recorded without consent" a state this machine cannot produce
rather than a mistake a reviewer has to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dsr.recording_consent import vocabulary as vocab
from dsr.recording_consent.errors import IllegalTransition, JoinWithoutConsentRefused

#: The steps the machine accepts, in the order they can happen.
STEPS: tuple[str, ...] = (
    "grant_consent",
    "decline_consent",
    "join_without_consent",
    "start_recording",
    "finish_recording",
    "cancel_recording",
)

#: The three decisions the research names, mapped to the step each one drives.
#:
#: The two vocabularies are kept apart on purpose. ``vocab.DECISIONS`` is what a
#: participant or the bot *reports* and it is the closed set the HTTP layer
#: validates against; ``STEPS`` is what the machine *does* and it also holds the
#: recording lifecycle, which no participant reports. Translating through a table
#: rather than by string surgery means a new decision cannot silently become a new
#: step, and an unknown one is refused rather than reaching the machine.
DECISION_STEPS: dict[str, str] = {
    "granted": "grant_consent",
    "declined": "decline_consent",
    "joined_without_consent": "join_without_consent",
}


@dataclass(frozen=True)
class Machine:
    """One resolved point in the machine, plus the transitions it allows.

    Frozen, because a machine state is a fact about a booking and a caller that
    could mutate one after the fact would make the stored ``state`` and the
    stored axes disagree. Everything downstream copies out of it.
    """

    state: str
    consent_state: str
    recording_state: str
    enforced: bool
    terminal: bool

    def to_data(self) -> dict[str, Any]:
        """The five fields stored on the record, and served to the frontend."""
        return {
            "state": self.state,
            "consent_state": self.consent_state,
            "recording_state": self.recording_state,
            "enforced": self.enforced,
            "terminal": self.terminal,
        }

    def allows(self, step: str) -> bool:
        return step in allowed_steps(self.state)


def allowed_steps(state: str) -> tuple[str, ...]:
    """Which steps are legal from a state.

    A step absent from this tuple is refused by :func:`advance` with a message
    naming both the state and the step, because a state machine whose rejection
    does not say where it was is a machine nobody can debug from a log.

    Two things are deliberately *not* in these tuples.

    ``start_recording`` is absent from every state whose ``recording_state`` is
    not ``armed``. ``declined`` and ``joined_without_consent`` both cancel the
    recording at the moment of the decision, so advertising a way to start it from
    there would offer the caller a step that is guaranteed to fail. The check in
    :func:`advance` still refuses it, so the guarantee does not depend on this
    table being right - the table just stops promising something untrue.

    ``finish_recording`` is absent from them for the same reason and with a
    sharper edge: there is no recording to finish.
    """
    return {
        # The unenforced path: consent is not required, the recording is armed, and
        # the call's own lifecycle runs. A decision may still arrive, because the
        # page was issued and its answer is kept as evidence.
        "scheduled": (
            "grant_consent",
            "decline_consent",
            "join_without_consent",
            "start_recording",
            "cancel_recording",
        ),
        "awaiting_consent": ("grant_consent", "decline_consent", "join_without_consent"),
        "consented": ("decline_consent", "cancel_recording", "start_recording"),
        # A decline under enforcement has already cancelled the recording, but the
        # call itself still runs and has to reach a terminal state. `cancel_recording`
        # is that step: the call ended and nothing was recorded.
        "declined": ("cancel_recording", "join_without_consent"),
        # Same, plus the two decisions. A participant who joined without consenting
        # and then consents on the call moves to `consented`, which is legitimate:
        # enforcement asks that consent was *explicitly given*, and it now has been.
        "joined_without_consent": ("grant_consent", "decline_consent", "cancel_recording"),
        "recording": ("finish_recording", "cancel_recording"),
        "recorded": (),
        "cancelled": (),
    }.get(state, ())


def consent_required(profile: dict[str, Any]) -> bool:
    """Whether anybody is asked for consent at all on this profile.

    Both switches, and the conjunction is the derivation above: the page has to be
    on for there to be a question, and enforcement has to be on for the answer to
    bind.
    """
    return bool(profile.get(vocab.CONSENT_PAGE_SWITCH) and profile.get(vocab.ENFORCEMENT_SWITCH))


def initial(profile: dict[str, Any]) -> Machine:
    """The state a booking is in the moment its consent link is issued.

    Two ways out of the gate, and they are chosen by the profile rather than by
    the caller: a profile that requires consent starts ``awaiting_consent`` with
    the recording ``blocked``, and every other profile starts ``scheduled`` with
    the recording ``armed``.

    The ``blocked`` state is the one that makes the gate visible before any call
    happens. Without it a booking that requires consent and one that does not
    would look identical until a participant answered, and the first sign of a
    broken consent gate would be a recording that should not exist.
    """
    enforced = consent_required(profile)
    if enforced:
        return Machine(
            state="awaiting_consent",
            consent_state="pending",
            recording_state="blocked",
            enforced=True,
            terminal=False,
        )
    return Machine(
        state="scheduled",
        consent_state="not_required",
        recording_state="armed",
        enforced=False,
        terminal=False,
    )


def _next_for_decision(decision: str, enforced: bool, allow_join_without_consent: bool) -> Machine:
    """Apply a participant's decision to the machine.

    Every branch here is the derivation stated in the module docstring. The two
    that depend on ``enforced`` are the open point, and the two that depend on
    ``allow_join_without_consent`` are the switch that has to have an effect.
    """
    if decision not in vocab.DECISIONS:
        raise IllegalTransition("unknown", f"decide {decision!r}", vocab.DECISIONS)

    if decision == "granted":
        return Machine("consented", "granted", "armed", enforced, False)

    if decision == "declined":
        if enforced:
            # "consent page enforcement cancels recording when declined"
            return Machine("declined", "declined", "cancelled", enforced, False)
        # Enforcement off: the page is advisory, so the decline is evidence and
        # the recording continues. The record says so in both fields.
        return Machine("scheduled", "declined", "armed", enforced, False)

    # join_without_consent
    if not allow_join_without_consent:
        raise JoinWithoutConsentRefused(
            "This consent profile does not allow participants to join without giving "
            "consent, so a participant who has not consented is not admitted. Enable "
            f"{vocab.JOIN_WITHOUT_CONSENT_SWITCH} to allow the join and cancel the "
            "recording instead.",
            {"join_without_consent": "is not allowed by this profile"},
        )
    if enforced:
        # Step 2: "Allow participants to join without giving consent (recording
        # will be canceled)". The parenthetical is the researched consequence.
        return Machine(
            "joined_without_consent", "joined_without_consent", "cancelled", enforced, False
        )
    return Machine("scheduled", "joined_without_consent", "armed", enforced, False)


def advance(
    current: Machine,
    step: str,
    profile: dict[str, Any] | None = None,
) -> Machine:
    """Move the machine one step and return the new state.

    ``profile`` is needed only for the two steps whose legality depends on a
    switch rather than on the state alone: joining without consent depends on
    ``allow_join_without_consent``. Every other transition is a function of the
    current state and the step, and is checked against
    :func:`allowed_steps` first.

    A step that is legal from the state but refused by the profile raises rather
    than returning a state, because "the participant was refused" and "the
    participant was admitted" are different facts about the call and conflating
    them would lose the only record that the gate ever fired.
    """
    if step not in STEPS:
        raise IllegalTransition(current.state, step, STEPS)

    if step in allowed_steps(current.state):
        if step == "grant_consent":
            return Machine("consented", "granted", "armed", current.enforced, False)
        if step == "decline_consent":
            return _next_for_decision("declined", current.enforced, _allow_join(profile))
        if step == "join_without_consent":
            return _next_for_decision(
                "joined_without_consent", current.enforced, _allow_join(profile)
            )
        if step == "start_recording":
            if current.recording_state != "armed":
                raise IllegalTransition(
                    current.state,
                    step,
                    ("states whose recording is armed",),
                )
            return Machine(
                "recording", current.consent_state, "in_progress", current.enforced, False
            )
        if step == "finish_recording":
            return Machine("recorded", current.consent_state, "complete", current.enforced, True)
        # cancel_recording
        return Machine("cancelled", current.consent_state, "cancelled", current.enforced, True)

    raise IllegalTransition(current.state, step, allowed_steps(current.state))


def available_steps(state: str, profile: dict[str, Any] | None = None) -> tuple[str, ...]:
    """The steps legal from this state *on this profile*.

    :func:`allowed_steps` is a property of the machine alone.
    :func:`available_steps` is the property of the machine plus the profile a booking
    is actually on, and it is the one a caller should offer.

    The difference is one step. ``join_without_consent`` is legal from every consent
    state, and whether a participant may actually join without consenting is a
    property of the profile. A consent page that offered a button the API would
    refuse with a 403 is a page that asks the participant to do something impossible,
    so the button is removed rather than the error being left to arrive.
    """
    steps = allowed_steps(state)
    if "join_without_consent" not in steps:
        return steps
    if _allow_join(profile):
        return steps
    return tuple(step for step in steps if step != "join_without_consent")


def _allow_join(profile: dict[str, Any] | None) -> bool:
    """Read the join-without-consent switch, defaulting to closed.

    Closed by default, and that default is deliberate rather than convenient: the
    switch is an opt-in that also cancels the recording, so a caller that forgets
    to pass the profile must get the safe answer rather than the permissive one.
    """
    return bool((profile or {}).get(vocab.JOIN_WITHOUT_CONSENT_SWITCH, False))


def machine_from_data(data: dict[str, Any]) -> Machine:
    """Rebuild a :class:`Machine` from a stored record.

    Every stored record is readable back into a machine so that a later step does
    not have to trust a caller to pass the current state consistently. An
    unreadable record raises :class:`IllegalTransition` rather than defaulting,
    because a silent default here would let a machine move from a state nobody
    actually stored.
    """
    state = str(data.get("state") or "")
    if state not in vocab.STATES:
        raise IllegalTransition(state or "<empty>", "read a stored record", vocab.STATES)
    consent_state = str(data.get("consent_state") or "not_required")
    recording_state = str(data.get("recording_state") or "armed")
    return Machine(
        state=state,
        consent_state=consent_state,
        recording_state=recording_state,
        enforced=bool(data.get("enforced", False)),
        terminal=bool(data.get("terminal", state in vocab.TERMINAL_STATES)),
    )


def is_consistent(machine: Machine) -> tuple[bool, str]:
    """Check that a machine state and its two axes tell the same story.

    The axes are stored separately so they can be queried, and separate storage
    is separate truth unless something checks it. A booking whose ``state`` says
    ``recorded`` while its ``recording_state`` says ``cancelled`` is a booking
    whose compliance record contradicts itself, and the only moment that is
    cheap to detect is here.

    The terminal check is an exact pairing rather than a set membership, because a
    membership test would accept ``recorded`` with a cancelled recording - which is
    precisely the contradiction this function exists to catch, and which is the one
    a reviewer would otherwise have to notice by eye.
    """
    terminal_pairing = {"recorded": "complete", "cancelled": "cancelled"}
    expected = terminal_pairing.get(machine.state)
    if expected is not None and machine.recording_state != expected:
        return (
            False,
            f"terminal state {machine.state!r} means recording {expected!r}, "
            f"but the recording is {machine.recording_state!r}",
        )
    if machine.state == "recording" and machine.recording_state != "in_progress":
        return False, "state 'recording' without an in-progress recording"
    if machine.state in (
        "awaiting_consent",
        "consented",
        "scheduled",
    ) and machine.consent_state not in (
        "pending",
        "granted",
        "declined",
        "joined_without_consent",
        "not_required",
    ):
        return False, f"state {machine.state!r} with consent {machine.consent_state!r}"
    if machine.consent_state in ("declined", "joined_without_consent") and machine.enforced:
        if machine.recording_state not in ("cancelled", "blocked"):
            return (
                False,
                f"enforced consent {machine.consent_state!r} with recording "
                f"{machine.recording_state!r}; enforcement cancels the recording",
            )
    return True, ""
