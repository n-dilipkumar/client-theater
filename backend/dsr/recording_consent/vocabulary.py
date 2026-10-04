"""The terms the research fixes by name, served as data.

Every value in this module is either quoted from the researched specification at
``docs/research/digital-sales-room-workflows/wf/WF-060.md`` or derived from it by
a rule stated here. Nothing is invented silently: each table carries the sentence
it came from, and each derivation says what it decided and what it rejected.

The specification is a research document, not a field list. It fixes the names
and the behaviours and leaves the state machine to the implementer, so the
derived tables here are the ones a reviewer should read first.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Admin centre > Data capture > Recording consent
# --------------------------------------------------------------------------- #

#: The consent profile is a named, described object an administrator creates with
#: "Add Profile". A profile is the unit the rest of the settings hang off, so it
#: is the first thing this workflow provisions.
PROFILE_COLLECTION = "wf060_consent_profile"

#: Step 3: "web conference providers (Zoom, Google Meet, Microsoft Teams,
#: Webex)". The research names four. The label is what the UI shows; the slug is
#: the key every other table uses.
PROVIDERS: dict[str, str] = {
    "zoom": "Zoom",
    "google_meet": "Google Meet",
    "microsoft_teams": "Microsoft Teams",
    "webex": "Webex",
}

#: Step 3: "per-provider link settings: Dynamic link, Static link, or Let host
#: decide". Three values, spelled as the research spells them.
LINK_KINDS: tuple[str, ...] = ("dynamic_link", "static_link", "host_decides")

#: Step 3: "Sets a default provider". Exactly one provider carries
#: ``is_default``, and the profile cannot be saved without one once a provider is
#: present. The alternative - a profile with two defaults - would make
#: "which conference does this booking use" unanswerable.
DEFAULT_PROVIDER_FIELD = "default_provider"

#: Step 3 also names a company logo and supported languages for the consent page.
#: They are carried on the profile so the consent page can render, and they are
#: not load-bearing for the recording decision.
CONSENT_PAGE_LOCALES: tuple[str, ...] = ("en", "en-gb", "de", "fr", "es", "ja")

# --------------------------------------------------------------------------- #
# Step 2: the two switches the whole workflow turns on
# --------------------------------------------------------------------------- #

#: "Admin turns the Consent page on". Without it the research names a documented
#: API failure: 409 "Conflict, e.g. consent page is not enabled in your company".
CONSENT_PAGE_SWITCH = "consent_page_enabled"

#: "then checks Enforce use of consent page so Gong only records meetings where
#: consent was explicitly given via the consent page to record the call".
#:
#: DERIVATION, and this is an open point the research does not answer. The
#: research states what enforcement *achieves* and never states what happens when
#: the box is clear. The reading implemented here: enforcement off makes the
#: consent page advisory. The page is still issued and a decision is still
#: recorded, because the compliance evidence is worth keeping, but the decision
#: no longer gates the recording. See :mod:`dsr.recording_consent.decisions`,
#: which is where the rule lives and where the rejected reading is recorded.
ENFORCEMENT_SWITCH = "enforce_consent_page"

#: "Optionally enables Allow participants to join without giving consent
#: (recording will be canceled)". The parenthesised clause is the researched
#: consequence, and it is the reason this switch cannot be read as
#: "recording is optional".
JOIN_WITHOUT_CONSENT_SWITCH = "allow_join_without_consent"

#: Step 4: "Admin switches on the pre-call email". One switch for the whole
#: message, because the research describes subject, body, signature and legal
#: footer as one customisable email rather than as four separately switchable
#: fields.
PRECALL_EMAIL_SWITCH = "precall_email_enabled"

#: Step 5: "Admin configures the audio prompt". The switch and the prompt are
#: separate because the research also gives a separate setting for whether the
#: prompt plays at all when the consent page was used.
AUDIO_PROMPT_SWITCH = "audio_prompt_enabled"

# --------------------------------------------------------------------------- #
# The researched API surface
# --------------------------------------------------------------------------- #
#
# The source is in beta: the research says "Meetings (in Beta Phase)". So the
# request and response shapes are treated as unversioned throughout, and the
# recorded outbound request is a faithful copy of what the research quotes
# rather than a contract this product can rely on.

#: The scope POST /v2/meetings requires.
MEETING_CREATE_SCOPE = "api:meetings:user:create"

#: POST /v2/meetings, the call a booking makes to get a consent-enabled link.
MEETINGS_ENDPOINT = "/v2/meetings"
MEETING_PATCH_ENDPOINT = "/v2/meetings/{meetingId}"
MEETINGS_DELETE_ENDPOINT = "/v2/meetings"
INTEGRATION_STATUS_ENDPOINT = "/v2/meetings/integration/status"

#: The request fields the research quotes for NewMeetingRequest.
NEW_MEETING_REQUEST_FIELDS: tuple[str, ...] = (
    "startTime",
    "endTime",
    "title",
    "invitees",
    "externalId",
    "organizerEmail",
)

#: The response fields the research quotes for NewMeetingResponse.
NEW_MEETING_RESPONSE_FIELDS: tuple[str, ...] = (
    "requestId",
    "meetingId",
    "meetingUrl",
    "additionalInvitees",
)

#: The two documented failures, quoted rather than paraphrased. 409 is the
#: consent page being off, which is a configuration state this workflow can read
#: before it calls. 404 is an organiser email with no user behind it, which is
#: the profile-resolution key: the research resolves the profile *by user*.
DOCUMENTED_ERRORS: dict[int, str] = {
    409: "Conflict, e.g. consent page is not enabled in your company",
    404: "No Gong user found corresponding to the provided organizer email",
}

#: "organizerEmail doc: The email address of the user creating the meeting, the
#: Gong consent page link will be used according to the settings of this user."
#:
#: So the profile is resolved on the organiser's email through the user
#: directory, never on the room and never on the booking. The key is named here
#: because it is the single most load-bearing decision in the package: resolving
#: on the room would silently apply one seller's profile to another seller's
#: meeting.
PROFILE_RESOLUTION_KEY = "organizer_email"

#: "additionalInvitees: Attendees the requesting party should add to the
#: invitation, this should support adding email addresses such as
#: coordinator@gong.io for Gong to schedule the recording of the meeting."
#:
#: That is the recording bot. It auto-joins, which is the whole of step 7's
#: "the recording bot joins automatically".
RECORDING_BOT_EMAIL = "coordinator@gong.io"

# --------------------------------------------------------------------------- #
# Step 4: the pre-call email
# --------------------------------------------------------------------------- #

#: "Automatically send pre-call emails to external invitees between 10 and 20
#: minutes before the call". A window, not a point, and the research gives both
#: ends. The planner fires once and records which end of the window it fired at,
#: so an operator can see the real lead time rather than a claim about it.
PRECALL_WINDOW_MINUTES: tuple[int, int] = (10, 20)

#: The variables the research names for the pre-call email: "sender name/company,
#: meeting title, meeting hour". Three, and no more. An unknown ``{{token}}`` in a
#: template is an error rather than a literal, because a template that silently
#: ships ``{{unknown}}`` to a buyer is worse than one that refuses to save.
PRECALL_EMAIL_VARIABLES: dict[str, str] = {
    "{{sender_name}}": "The name of the person who invited the participant.",
    "{{sender_company}}": "The company of the person who invited the participant.",
    "{{meeting_title}}": "The title of the meeting.",
    "{{meeting_hour}}": "The hour the meeting starts, in the recipient's timezone.",
}

#: Step 4 also names a signature and a legal footer. Both are free text and both
#: are part of the compliance surface, so they are stored on the profile.
PRECALL_EMAIL_TEXT_FIELDS: tuple[str, ...] = ("subject", "body", "signature", "legal_footer")

#: Step 4: "external invitees". An invitee on the organiser's own domain is
#: internal and is not sent a pre-call email, because the disclosure has already
#: happened in the room.
PRECALL_EMAIL_AUDIENCE = "external"

# --------------------------------------------------------------------------- #
# Step 5: the audio prompt
# --------------------------------------------------------------------------- #

#: The researched prompt text, quoted.
DEFAULT_PROMPT_TEXT = (
    "We're recording this call for better-note taking, following up and training purposes"
)

#: "audio prompt fires on first guest with audio on (or on every guest)". The
#: research gives both and chooses neither.
#:
#: DERIVATION: ``first_guest_with_audio`` is implemented. The prompt is a
#: disclosure to the room, and a disclosure heard once has been made; replaying it
#: per participant turns a legal notice into an argument that continues after the
#: person joining the call has already been told. The rejected alternative,
#: ``every_guest``, is retained in the vocabulary and accepted by the validator,
#: so an administrator who wants it can have it - the choice is recorded rather
#: than hidden.
PROMPT_MODES: tuple[str, ...] = ("first_guest_with_audio", "every_guest")
DEFAULT_PROMPT_MODE = "first_guest_with_audio"

#: "Don't play the audio prompt if the consent page is used". When the consent
#: page already carried the same disclosure, the audio prompt is redundant, and
#: playing it after the participant consented is a contradiction they can see.
SUPPRESS_PROMPT_WHEN_CONSENT_PAGE_USED = "suppress_prompt_when_consent_page_used"

# --------------------------------------------------------------------------- #
# The derived state machine
# --------------------------------------------------------------------------- #
#
# The research names "join without consent" and "recording will be canceled" and
# never joins them into a machine. :mod:`dsr.recording_consent.decisions` owns the
# rule; these are the names the machine uses, so a stored state is always one of
# these strings and the frontend can rely on the set without guessing.

#: The eight states. ``state`` on a record is always exactly one of these.
STATES: tuple[str, ...] = (
    "scheduled",
    "awaiting_consent",
    "consented",
    "declined",
    "joined_without_consent",
    "recording",
    "recorded",
    "cancelled",
)

#: The consent axis, stored separately so it is queryable through the dynamic
#: index with a plain ``find()``. ``not_required`` is the state a booking is in
#: when the consent page is off or enforcement is off: the page was not asked of
#: anybody, so there is no decision to hold.
CONSENT_STATES: tuple[str, ...] = (
    "not_required",
    "pending",
    "granted",
    "declined",
    "joined_without_consent",
)

#: The recording axis. ``blocked`` is the researched state that makes the gate
#: visible before any call happens: consent is outstanding, so nothing may record.
RECORDING_STATES: tuple[str, ...] = (
    "blocked",
    "armed",
    "in_progress",
    "complete",
    "cancelled",
)

#: The four decisions a participant or the recording bot can report. They are the
#: inputs to the machine and nothing else.
DECISIONS: tuple[str, ...] = ("granted", "declined", "joined_without_consent")

#: The terminal states: a booking that has been scheduled and has finished, one
#: way or the other. A seller reads these two and nothing in between.
TERMINAL_STATES: tuple[str, ...] = ("recorded", "cancelled")

# --------------------------------------------------------------------------- #
# Stale-link invalidation
# --------------------------------------------------------------------------- #

#: "Each time you change this link, the previous link is disabled."
#:
#: DERIVATION: implemented as a state on the link record, not a delete. The brief
#: requires it - "so the audit row survives" - and the reason is that a disabled
#: link is evidence. A booking whose conferencing link was swapped twice needs the
#: first link readable afterwards, because the question "which link did the buyer
#: actually join" is exactly what a recording dispute turns on.
LINK_STATES: tuple[str, ...] = ("active", "superseded")

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: One record per booking. Jev chose this shape over two collections and over an
#: append-only event log (audit jev-20261004T064834-23344-14922, confidence
#: 0.80): a booking produces a handful of rows rather than a stream, and both a
#: seller and a participant read the state on a page.
CONSENT_RECORDING_COLLECTION = "wf060_consent_recording"

#: The user directory. Step 6: "Admin assigns the profile to users; the profile
#: is set as default for new team members", and the data sources name "Gong user
#: directory + per-user settings (import emails, record-by-Gong flag, 'if the
#: invitation of this user to a web conference will prevent its recording')".
DIRECTORY_COLLECTION = "wf060_directory_user"

#: The pre-call email the planner produced. Written so an operator can read what
#: a participant was told, and when.
PRECALL_EMAIL_COLLECTION = "wf060_precall_email"

#: The recording lifecycle, kept apart from the consent lifecycle because they
#: answer different questions: "did they agree" and "did we get a recording".
RECORDING_RUN_COLLECTION = "wf060_recording_run"

#: Every collection this workflow owns, for the isolation test.
ALL_COLLECTIONS: tuple[str, ...] = (
    CONSENT_RECORDING_COLLECTION,
    DIRECTORY_COLLECTION,
    PRECALL_EMAIL_COLLECTION,
    RECORDING_RUN_COLLECTION,
    PROFILE_COLLECTION,
)

#: Where the previewed consent page lives: step 3's company logo and supported
#: languages, plus the profile description the admin typed. Held as one record so
#: the preview route has something to render.
CONSENT_PAGE_COLLECTION = "wf060_consent_page"
