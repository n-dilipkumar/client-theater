"""WF-060: auto-join and record the meeting, gated by recording consent.

The workflow records a booked meeting only after a participant accepts a consent
page, and a recording bot joins the call automatically. The researched
specification is ``docs/research/digital-sales-room-workflows/wf/WF-060.md``, and
it is explicit that it is not a field list: "This spec does not state the flow as
separate fields. The evidence below is the specification. An implementer who needs
a flow the evidence does not contain must derive it and record the derivation,
not assume it."

So this package's first job is the derivation, and :mod:`~dsr.recording_consent.inferences`
records it as data. The ten entries there are every judgement call, each with the
evidence it rests on, the reading it rejected, what that reading would have cost,
and the residual risk. ``GET /wf-060/decisions`` serves them.

Module map, in dependency order:

``vocabulary``
    The researched terms: the four web conference providers, the three per-provider
    link kinds, the two switches step 2 turns on, the quoted API surface and its two
    documented failures, the pre-call window, the audio prompt modes, and the closed
    set of state names.
``errors``
    One hierarchy, so the feature module registers one handler rather than four.
``profiles``
    Steps 1 through 5. A payload validated into a normalised dict, so the rules can
    be tested without a request and a schema-flexible payload never becomes an
    unvalidated dict.
``directory``
    Step 6, and the key the profile resolves on.
``decisions``
    The derived state machine, and the open point the research does not answer.
``links``
    Step 7's consent-enabled link, the exact outbound request the research quotes,
    and stale-link invalidation as a state.
``precall``
    Step 4's pre-call email: the 10-to-20-minute window, the external audience, and
    the researched variables.
``inferences``
    Every judgement call, named and served.
``engine``
    The facade the HTTP layer calls, and the only module that stores anything.

Three boundaries are worth stating before reading any of it.

**This package does not import another feature.** It reads and writes only the five
collections in :data:`~dsr.recording_consent.vocabulary.ALL_COLLECTIONS`. A consent
profile, a user directory entry and a recording run are objects this workflow owns
outright, and two features authored independently must not share a Python module.

**Nothing here opens a socket.** The researched endpoint is Gong's and its own
documentation says the API is "in Beta Phase", so the request and response shapes
are treated as unversioned throughout. What is real is the decision: which profile
applies, whether the consent page is on, whether the call may be recorded at all,
what the state machine does next, and the exact outbound request that would carry
it. :func:`~dsr.recording_consent.links.new_meeting_request` stores that request on
the record, so a reviewer can read the researched call without a network call
happening.

**The recording bot is not a state.** A recording that is ``blocked`` is released
or cancelled, never started, so this machine cannot produce a booking that was
recorded without the consent its profile required. That is the property the whole
workflow exists to provide, and it is enforced by
:func:`~dsr.recording_consent.decisions.advance` rather than by review.
"""

from __future__ import annotations

from dsr.recording_consent import (
    decisions,
    directory,
    inferences,
    links,
    precall,
    profiles,
    vocabulary,
)
from dsr.recording_consent.decisions import (
    DECISION_STEPS,
    STEPS,
    Machine,
    advance,
    allowed_steps,
    available_steps,
    consent_required,
    initial,
    is_consistent,
    machine_from_data,
)
from dsr.recording_consent.directory import (
    can_record,
    invitee_address,
    normalise_email,
    normalise_user,
    recording_blocked_by_invitee,
    resolve,
)
from dsr.recording_consent.engine import ConsentEngine
from dsr.recording_consent.errors import (
    BookingNotFound,
    ConsentError,
    ConsentPageDisabled,
    IllegalTransition,
    JoinWithoutConsentRefused,
    LinkSuperseded,
    OrganizerUnmapped,
    ProfileInvalid,
    ProfileNotFound,
)
from dsr.recording_consent.inferences import INFERENCES, by_id, describe, describe_one
from dsr.recording_consent.links import (
    active_link,
    consent_page_preview,
    link_record,
    new_meeting_request,
    new_meeting_response,
    prompt_suppressed,
    retire_previous,
)
from dsr.recording_consent.precall import (
    is_external,
    recipients,
    render,
    should_send,
    window,
)
from dsr.recording_consent.profiles import normalise, patch
from dsr.recording_consent.vocabulary import (
    ALL_COLLECTIONS,
    AUDIO_PROMPT_SWITCH,
    CONSENT_PAGE_SWITCH,
    CONSENT_RECORDING_COLLECTION,
    CONSENT_STATES,
    DEFAULT_PROMPT_MODE,
    DIRECTORY_COLLECTION,
    DOCUMENTED_ERRORS,
    ENFORCEMENT_SWITCH,
    JOIN_WITHOUT_CONSENT_SWITCH,
    LINK_KINDS,
    LINK_STATES,
    MEETING_CREATE_SCOPE,
    NEW_MEETING_REQUEST_FIELDS,
    NEW_MEETING_RESPONSE_FIELDS,
    PRECALL_EMAIL_COLLECTION,
    PRECALL_EMAIL_SWITCH,
    PRECALL_EMAIL_VARIABLES,
    PRECALL_WINDOW_MINUTES,
    PROFILE_COLLECTION,
    PROFILE_RESOLUTION_KEY,
    PROMPT_MODES,
    PROVIDERS,
    RECORDING_BOT_EMAIL,
    RECORDING_RUN_COLLECTION,
    RECORDING_STATES,
    STATES,
)

__all__ = [
    "ALL_COLLECTIONS",
    "AUDIO_PROMPT_SWITCH",
    "BookingNotFound",
    "CONSENT_PAGE_SWITCH",
    "CONSENT_RECORDING_COLLECTION",
    "CONSENT_STATES",
    "ConsentEngine",
    "ConsentError",
    "ConsentPageDisabled",
    "DEFAULT_PROMPT_MODE",
    "DECISION_STEPS",
    "DIRECTORY_COLLECTION",
    "DOCUMENTED_ERRORS",
    "ENFORCEMENT_SWITCH",
    "INFERENCES",
    "IllegalTransition",
    "JoinWithoutConsentRefused",
    "JOIN_WITHOUT_CONSENT_SWITCH",
    "LINK_KINDS",
    "LINK_STATES",
    "LinkSuperseded",
    "MEETING_CREATE_SCOPE",
    "Machine",
    "NEW_MEETING_REQUEST_FIELDS",
    "NEW_MEETING_RESPONSE_FIELDS",
    "OrganizerUnmapped",
    "PRECALL_EMAIL_COLLECTION",
    "PRECALL_EMAIL_SWITCH",
    "PRECALL_WINDOW_MINUTES",
    "PROFILE_COLLECTION",
    "PROFILE_RESOLUTION_KEY",
    "PROMPT_MODES",
    "PROVIDERS",
    "PRECALL_EMAIL_VARIABLES",
    "ProfileInvalid",
    "ProfileNotFound",
    "RECORDING_BOT_EMAIL",
    "RECORDING_RUN_COLLECTION",
    "RECORDING_STATES",
    "STATES",
    "STEPS",
    "active_link",
    "advance",
    "allowed_steps",
    "available_steps",
    "by_id",
    "can_record",
    "consent_page_preview",
    "consent_required",
    "decisions",
    "describe",
    "describe_one",
    "directory",
    "initial",
    "inferences",
    "invitee_address",
    "is_consistent",
    "is_external",
    "links",
    "link_record",
    "machine_from_data",
    "new_meeting_request",
    "new_meeting_response",
    "normalise",
    "normalise_email",
    "normalise_user",
    "patch",
    "precall",
    "profiles",
    "prompt_suppressed",
    "recipients",
    "recording_blocked_by_invitee",
    "render",
    "resolve",
    "retire_previous",
    "should_send",
    "vocabulary",
    "window",
]
