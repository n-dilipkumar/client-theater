"""Every researched term WF-078 enforces against, with the evidence it came from.

This is the researched specification for WF-078 made executable, and the only place a
constant named after the specification lives. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-078.md``, quoted in full in issue 172.
Every value below is either quoted from that document or derived from a quote by a
derivation recorded in :mod:`dsr.identity_verification.inferences`. Nothing here is a
house opinion.

The sentence that governs the whole workflow
-------------------------------------------

The specification's data flow says the document body "is withheld until it clears", and
its automation section says verification "is re-asserted by the gate on every attempt;
there is no 'verify once, remember forever' behaviour in these flows". Those two
sentences together are the contract: this workflow withholds a body, and it withholds it
again on the next attempt. A pass is not a key that unlocks the document permanently.

What this build does not claim
------------------------------

Three of the specification's own data sources are marked inferred, and the markers are
the specification's:

* "public-record data source for KBA `[inferred - the docs name kba_verification as
  'identity questions generated from public records' but no vendor]`"
* "ID-verification provider for id_verification `[inferred]`"
* "Recipient settings panel in the PandaDoc editor (verification method + timing)
  `[inferred from the documented verification_settings object]`"

So the KBA question source and the ID provider are assumptions, not sourced facts, and
the panel that sets them is an assumption about a screen rather than about the object.
:data:`ASSUMPTION` carries those three to every response, so a reader of any answer from
this workflow learns which parts are evidenced and which parts this build assumed.

Who owns the authentication decision
------------------------------------

The specification quotes the vendor on this and the quote is unambiguous: "Ultimately,
however, you are solely responsible for making sure that your signer/end user
authentication process is sufficient and complies with any and all applicable laws and
regulations." So this room owns the decision. :data:`AUTHENTICATION_OWNER` says so in
every response, and nothing in this package delegates the decision to a provider by
default.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one `records` table and
# `find()` matches on collection before it matches on anything else.

DOCUMENT_COLLECTION = "wf078_verified_document"
RECIPIENT_COLLECTION = "wf078_verified_recipient"
ATTEMPT_COLLECTION = "wf078_verification_attempt"
SESSION_COLLECTION = "wf078_verification_session"
CODE_COLLECTION = "wf078_sms_code"

ALL_COLLECTIONS = (
    DOCUMENT_COLLECTION,
    RECIPIENT_COLLECTION,
    ATTEMPT_COLLECTION,
    SESSION_COLLECTION,
    CODE_COLLECTION,
)

#: The payload-side twin of the envelope's ``room_id``.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the
#: record *envelope*, so the store strips it out of ``data`` before the dynamic index is
#: built. A record that stored its room there would be unfilterable by ``find()``, and a
#: "filter by room" that silently returns nothing is the kind of defect that ships. The
#: envelope still carries ``room_id``; every response projects this key back to it.
ROOM_REF = "room_ref"


# --------------------------------------------------------------------------- #
# The two gates
# --------------------------------------------------------------------------- #
#
# The specification's timing table names both, and names who each one applies to:
#
#   "before_open | Before the recipient can view the document | All recipients"
#   "before_sign | Before the recipient can sign | Signers only"
#
# So the audience difference is sourced, not derived. `before_sign` applies to signers
# only, and a recipient who is not a signer cannot carry one. That is enforced in
# dsr.identity_verification.rules and tested there.

BEFORE_OPEN = "before_open"
BEFORE_SIGN = "before_sign"

PLACES = (BEFORE_OPEN, BEFORE_SIGN)

PLACE_DESCRIPTIONS = {
    BEFORE_OPEN: "Before the recipient can view the document",
    BEFORE_SIGN: "Before the recipient can sign",
}

#: Who each gate applies to, quoted from the same table. The third column of the
#: specification's table is the audience, and it differs between the two rows.
PLACE_AUDIENCE = {
    BEFORE_OPEN: "all_recipients",
    BEFORE_SIGN: "signers_only",
}

PLACE_LABELS = {
    BEFORE_OPEN: "Before open",
    BEFORE_SIGN: "Before sign",
}


# --------------------------------------------------------------------------- #
# Recipient roles
# --------------------------------------------------------------------------- #
#
# The audience column is sourced ("Signers only"), the word for a recipient who is a
# signer is not: the specification names no role vocabulary. DERIVED_RECIPIENT_ROLE
# records the choice and what the alternatives would have cost.

ROLE_SIGNER = "signer"
ROLE_RECIPIENT = "recipient"

ROLES = (ROLE_SIGNER, ROLE_RECIPIENT)

ROLE_LABELS = {
    ROLE_SIGNER: "Signer",
    ROLE_RECIPIENT: "Recipient",
}

#: The role each gate requires. Straight from the sourced audience column.
PLACE_REQUIRED_ROLE = {
    BEFORE_OPEN: None,  # "All recipients": no role requirement
    BEFORE_SIGN: ROLE_SIGNER,
}


# --------------------------------------------------------------------------- #
# The four methods
# --------------------------------------------------------------------------- #
#
# The specification names the method table and quotes each entry:
#
#   passcode_verification.passcode        "It must be 6-100 characters with at least one
#                                         letter and one digit"
#   phone_verification.phone_number       "must be in international format
#                                         (e.g., +1555667890)"
#   kba_verification.enabled              "The recipient answers identity questions
#                                         generated from public records"
#   id_verification.enabled               "The recipient verifies identity with a
#                                         government-issued ID"
#
# The four are a discriminated union on the recipient: exactly one of them is active for
# a given gate, and the active one decides which payload an attempt must carry.

METHOD_PASSCODE = "passcode"
METHOD_SMS = "sms"
METHOD_KBA = "kba"
METHOD_ID = "id"

METHODS = (METHOD_PASSCODE, METHOD_SMS, METHOD_KBA, METHOD_ID)

METHOD_LABELS = {
    METHOD_PASSCODE: "Typed passcode",
    METHOD_SMS: "SMS one-time password",
    METHOD_KBA: "Knowledge-based authentication",
    METHOD_ID: "Government-issued ID check",
}

METHOD_DESCRIPTIONS = {
    METHOD_PASSCODE: "The recipient types a passcode the sender chose.",
    METHOD_SMS: "The recipient types a one-time code the room sent to their phone.",
    METHOD_KBA: "The recipient answers identity questions the sender recorded.",
    METHOD_ID: "The recipient states the details of a government-issued ID.",
}

#: The vendor's own field name for each method, quoted from the method table. Reproducing
#: them is what lets a caller tell a PandaDoc `verification_settings` object from this
#: room's without a lookup table.
METHOD_VENDOR_FIELDS = {
    METHOD_PASSCODE: "passcode_verification",
    METHOD_SMS: "phone_verification",
    METHOD_KBA: "kba_verification",
    METHOD_ID: "id_verification",
}

#: The key each method reads its configuration from inside a gate entry. One per method,
#: so a gate entry names its method by which key it carries rather than by a second field
#: that could disagree with it. A union with a discriminator that disagrees with its own
#: payload is the defect this shape prevents.
METHOD_CONFIG_KEYS = {
    METHOD_PASSCODE: "passcode",
    METHOD_SMS: "phone_number",
    METHOD_KBA: "questions",
    METHOD_ID: "id_document",
}

#: Whether the method needs something sent to the recipient before an attempt can be
#: evaluated. The specification's Dropbox Sign evidence says the signer "can select the
#: 'Send code' button to receive a 6-digit code", which is a delivery step this room owns.
METHOD_NEEDS_DELIVERY = {
    METHOD_PASSCODE: False,
    METHOD_SMS: True,
    METHOD_KBA: False,
    METHOD_ID: False,
}


# --------------------------------------------------------------------------- #
# Passcode: the exact bound the specification states
# --------------------------------------------------------------------------- #

PASSCODE_MIN_LENGTH = 6
PASSCODE_MAX_LENGTH = 100

#: "at least one letter and one digit", quoted. Enforced as two separate rules so the
#: message says which one failed, because a seller who typed a 12-character all-letter
#: string needs to be told that rather than told it was too short.
PASSCODE_REQUIRES_LETTER = True
PASSCODE_REQUIRES_DIGIT = True

PASSCODE_RULE_TEXT = (
    "A passcode must be 6 to 100 characters, with at least one letter and at least one digit."
)


# --------------------------------------------------------------------------- #
# Phone: the exact format the specification states
# --------------------------------------------------------------------------- #
#
# "must be in international format (e.g., +1555667890)". The example is E.164: a plus,
# then a non-zero country digit, then up to fourteen more digits. DERIVED_E164_PRECISE
# records why this build reads the example as a format rather than as a sample.

PHONE_PLACEHOLDER = "+1555667890"
PHONE_PREFIX = "+"
PHONE_MIN_DIGITS = 8
PHONE_MAX_DIGITS = 15

PHONE_RULE_TEXT = (
    "A phone number must be in international format, written as a plus and the country "
    "code, for example +1555667890."
)


# --------------------------------------------------------------------------- #
# SMS: authentication and delivery are separate
# --------------------------------------------------------------------------- #
#
# The specification's extensibility note is explicit: Dropbox Sign separates
# "authentication" from "delivery" via `sms_phone_number_type`, "so an SMS can be an auth
# factor, a delivery channel, or both". Three states, all modelled, all tested.

SMS_TYPE_AUTHENTICATION = "authentication"
SMS_TYPE_DELIVERY = "delivery"
SMS_TYPE_BOTH = "both"

SMS_TYPES = (SMS_TYPE_AUTHENTICATION, SMS_TYPE_DELIVERY, SMS_TYPE_BOTH)

SMS_TYPE_MEANINGS = {
    SMS_TYPE_AUTHENTICATION: "The number carries the one-time code and nothing else.",
    SMS_TYPE_DELIVERY: "The number receives the document and is not an authentication factor.",
    SMS_TYPE_BOTH: "The number receives the document and carries the one-time code.",
}

#: The code the specification's evidence describes: "a unique SMS authentication code
#: that is sent to the signer once the signature request is submitted", delivered as "a
#: 6-digit code via text message". Six digits is what both cited sources say.
SMS_CODE_DIGITS = 6

#: "they can request the code to be sent again if needed". The evidence names the ability
#: and not a cap on it, so this build lets a code be re-sent and records each one rather
#: than refusing the second request. DERIVED_SMS_RESEND records that.
SMS_RESEND_ALLOWED = True

SMS_TYPE_FIELD = "sms_phone_number_type"


# --------------------------------------------------------------------------- #
# KBA
# --------------------------------------------------------------------------- #

KBA_MIN_QUESTIONS = 1
KBA_MAX_QUESTIONS = 10

#: "identity questions generated from public records". The source is inferred, so this
#: build does not generate anything: the sender records the questions and their expected
#: answers, and the room compares what the recipient typed. DERIVED_KBA_QUESTION_SOURCE
#: records that and what it costs.
KBA_QUESTION_FIELDS = ("prompt", "answer")
KBA_PROMPT_FIELD = "prompt"
KBA_ANSWER_FIELD = "answer"


# --------------------------------------------------------------------------- #
# ID check
# --------------------------------------------------------------------------- #

ID_DOCUMENT_FIELDS = ("document_type", "document_number", "issuing_country")

#: The two fields a check cannot be run without. The third is compared when the sender
#: records it and skipped when they do not: a sender who knows the issuing country should
#: be asked for it, and a sender who does not should not have to invent one. The prompt
#: reports whichever fields were actually recorded, so a recipient is never asked for
#: something the sender did not store.
ID_DOCUMENT_REQUIRED_FIELDS = ("document_type", "document_number")

#: The evidence names "a government-issued ID" and no provider, so this build checks the
#: details the recipient states against the details the sender recorded, and says so.
#: DERIVED_ID_PROVIDER records that and what it costs.
ID_DOCUMENT_TYPES = ("passport", "national_id", "drivers_licence", "residence_permit")


# --------------------------------------------------------------------------- #
# Outcomes and attempt states
# --------------------------------------------------------------------------- #

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"

OUTCOMES = (OUTCOME_PASS, OUTCOME_FAIL)

#: Why an attempt failed, so a rejected attempt is as legible as a successful one. The
#: specification's requirement is that "a rejected attempt is as visible as a successful
#: one", and a failure row with no reason is only half a record.
REASON_NO_CONFIG = "no_verification_configured"
REASON_WRONG_PASSCODE = "passcode_mismatch"
REASON_MALFORMED_PASSCODE = "passcode_not_provable"
REASON_NO_CODE_SENT = "no_code_sent"
REASON_WRONG_CODE = "code_mismatch"
REASON_ANSWERS_INCOMPLETE = "answers_incomplete"
REASON_ANSWER_MISMATCH = "answer_mismatch"
REASON_ID_MISMATCH = "id_details_mismatch"
REASON_NOT_A_SIGNER = "recipient_is_not_a_signer"
REASON_GATE_NOT_ON_THIS_RECIPIENT = "gate_does_not_apply_to_this_recipient"

FAILURE_REASONS = (
    REASON_NO_CONFIG,
    REASON_WRONG_PASSCODE,
    REASON_MALFORMED_PASSCODE,
    REASON_NO_CODE_SENT,
    REASON_WRONG_CODE,
    REASON_ANSWERS_INCOMPLETE,
    REASON_ANSWER_MISMATCH,
    REASON_ID_MISMATCH,
    REASON_NOT_A_SIGNER,
    REASON_GATE_NOT_ON_THIS_RECIPIENT,
)


# --------------------------------------------------------------------------- #
# Withholding
# --------------------------------------------------------------------------- #

BODY_WITHHELD = "withheld"
BODY_RELEASED = "released"

#: The two reasons a body is withheld, both sourced from the data flow's one sentence
#: "the document body is withheld until it clears".
WITHHELD_REASON_GATE_UNCLEARED = "verification_gate_not_cleared"
WITHHELD_REASON_REASSERTED = "verification_gate_reasserted"


# --------------------------------------------------------------------------- #
# What this workflow is and is not worth
# --------------------------------------------------------------------------- #

#: Rendered on the page and carried in every response, because the cheap way to keep a
#: claim honest is to make it part of the data rather than a line of copy somebody can
#: delete.
LIMITATION = (
    "Verification raises the cost of opening a document under the wrong identity. It does "
    "not prove who is holding the phone, and the knowledge-based and ID checks compare what "
    "a recipient typed against what a sender recorded. Neither one is a background check."
)

NOT_PROOF = (
    "A pass records that the right answer was given, not that the person who gave it is "
    "who the sender meant."
)

#: The three assumptions the specification itself marks inferred, carried to every caller.
ASSUMPTION = (
    "Three parts of this workflow are assumptions, not sourced facts. The public-record "
    "source behind knowledge-based questions names no vendor, so this room records the "
    "questions a sender wrote rather than generating any. The provider behind the ID check "
    "names none either, so this room compares the details a recipient stated against the "
    "details a sender recorded. And the sender-side panel that sets a method and a moment is "
    "an assumption about a screen; the verification settings object it edits is sourced."
)

#: Quoted from the specification's extensibility note, and the reason this package never
#: delegates the decision to a provider by default.
AUTHENTICATION_OWNER = (
    "This room owns the authentication decision. The vendor's own guidance is that you are "
    "solely responsible for making sure that your signer or end-user authentication process "
    "is sufficient and complies with any and all applicable laws and regulations."
)

#: The keys every response carries, so a caller cannot read a gate without also reading
#: what the gate is worth and which parts of it are assumed.
#:
#: ``not_sourced`` and not ``assumptions``, because a response that also lists the recorded
#: assumptions would have two keys fighting over one name, and whichever was assigned last
#: would silently win. A test caught exactly that: the vocabulary's ``assumptions`` string
#: overwrote the route's own list of them.
OWNER_FIELD = "authentication_owner"
ASSUMPTION_FIELD = "not_sourced"
LIMITATION_FIELD = "limitation"
NOT_PROOF_FIELD = "not_proof"


# --------------------------------------------------------------------------- #
# Where the integer codes come from
# --------------------------------------------------------------------------- #

#: The band the specification cites for verification outcomes: "Audit codes are emitted
#: automatically per outcome, including failures (47-54, 69, 70)". These are integers
#: another workflow owns, and they are published here as a reference so a reviewer can
#: check the mapping without opening that workflow's package. Nothing here mints one.
VERIFICATION_CODE_BAND = (47, 54)
EMAIL_OTP_CODE_BAND = (69, 70)

#: Where the codes are defined. Named rather than duplicated, because the table has to
#: have exactly one home.
CODE_TABLE_OWNER = "dsr.audit_export.vocabulary.verification_code"

CODE_OWNER_NOTE = (
    "The integer action codes for a verification outcome are not defined here. They live in "
    "dsr.audit_export.vocabulary, which already lays out 47 to 50 as four pass codes and 51 "
    "to 54 as four fail codes over the method order kba, passcode, sms, id. This workflow "
    "carries the facts of an attempt and lets that table turn them into an integer, so the "
    "enum has one definition."
)


def vocabulary_payload() -> dict[str, Any]:
    """Everything this module asserts, for a reviewer and for the page.

    Served by ``GET /api/wf-078/vocabulary`` so the panel cannot drift from the rules
    that validate it: the two gates, the four methods, the passcode bound, the E.164
    bound, the SMS role axis and the honesty sentences all come from the same tables the
    validator reads.
    """

    return {
        "places": [
            {
                "id": place,
                "label": PLACE_LABELS[place],
                "description": PLACE_DESCRIPTIONS[place],
                "audience": PLACE_AUDIENCE[place],
                "required_role": PLACE_REQUIRED_ROLE[place],
            }
            for place in PLACES
        ],
        "methods": [
            {
                "id": method,
                "label": METHOD_LABELS[method],
                "description": METHOD_DESCRIPTIONS[method],
                "vendor_field": METHOD_VENDOR_FIELDS[method],
                "config_key": METHOD_CONFIG_KEYS[method],
                "needs_delivery": METHOD_NEEDS_DELIVERY[method],
            }
            for method in METHODS
        ],
        "roles": [{"id": role, "label": ROLE_LABELS[role]} for role in ROLES],
        "passcode": {
            "min_length": PASSCODE_MIN_LENGTH,
            "max_length": PASSCODE_MAX_LENGTH,
            "requires_letter": PASSCODE_REQUIRES_LETTER,
            "requires_digit": PASSCODE_REQUIRES_DIGIT,
            "rule": PASSCODE_RULE_TEXT,
        },
        "phone": {
            "prefix": PHONE_PREFIX,
            "example": PHONE_PLACEHOLDER,
            "min_digits": PHONE_MIN_DIGITS,
            "max_digits": PHONE_MAX_DIGITS,
            "rule": PHONE_RULE_TEXT,
        },
        "sms": {
            "types": [
                {"id": sms_type, "meaning": SMS_TYPE_MEANINGS[sms_type]} for sms_type in SMS_TYPES
            ],
            "field": SMS_TYPE_FIELD,
            "code_digits": SMS_CODE_DIGITS,
            "resend_allowed": SMS_RESEND_ALLOWED,
        },
        "kba": {
            "min_questions": KBA_MIN_QUESTIONS,
            "max_questions": KBA_MAX_QUESTIONS,
            "fields": list(KBA_QUESTION_FIELDS),
        },
        "id_check": {"fields": list(ID_DOCUMENT_FIELDS), "document_types": list(ID_DOCUMENT_TYPES)},
        "outcomes": list(OUTCOMES),
        "failure_reasons": list(FAILURE_REASONS),
        "body_states": [BODY_WITHHELD, BODY_RELEASED],
        "collections": list(ALL_COLLECTIONS),
        "code_bands": {
            "verification": list(VERIFICATION_CODE_BAND),
            "email_otp": list(EMAIL_OTP_CODE_BAND),
        },
        "code_table_owner": CODE_TABLE_OWNER,
        "code_owner_note": CODE_OWNER_NOTE,
        OWNER_FIELD: AUTHENTICATION_OWNER,
        ASSUMPTION_FIELD: ASSUMPTION,
        LIMITATION_FIELD: LIMITATION,
        NOT_PROOF_FIELD: NOT_PROOF,
    }
