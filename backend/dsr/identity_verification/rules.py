"""The two independent axes, the bounds the specification states, and one attempt.

Everything in this module is a rule, not a framework. It reads a mapping and returns a
mapping, raises one of four error types, and knows nothing about HTTP, SQLite or a
request. That is what makes every rule below testable without a server, and it is why the
HTTP layer in ``dsr.features.wf078_require_recipient_identity_verification`` is a thin
translation of what happens here.

The two axes, and why they are two
----------------------------------

The specification's extensibility note says the design it found was "method as a
discriminated union on the recipient (passcode / phone / KBA / ID) with an independent
timing axis (``before_open`` vs ``before_sign``) - the same recipient object can be
verified differently for viewing and signing".

That sentence is the design, and it has an exact consequence: **the same recipient may
carry two different settings at once, one per gate.** A passcode before the document
opens and a knowledge-based check before it is signed are two gates on one recipient, not
one gate that changed its mind. A shape that stores one method and one moment per
recipient cannot express it, so :func:`build_settings` returns a mapping keyed by gate and
every rule below reads it that way.

The audience difference is sourced, not chosen
---------------------------------------------

The specification's timing table names both gates and names who each applies to:
"``before_open`` | Before the recipient can view the document | **All recipients**" and
"``before_sign`` | Before the recipient can sign | **Signers only**". So
:func:`settings_for` refuses to attach a ``before_sign`` gate to a recipient who is not a
signer, and :func:`evaluate` returns a failure rather than a pass for one. A gate that
applies to nobody is a gate nobody reviews, and a before_sign gate quietly protecting
non-signers is the more dangerous half of that.

The two bounds the specification states exactly
-----------------------------------------------

**Passcode.** "It must be 6-100 characters with at least one letter and one digit."
:func:`validate_passcode` enforces the length at both bounds, the letter and the digit as
two separate findings, and refuses a passcode that is whitespace rather than lengthening
it into something valid.

**Phone.** "must be in international format (e.g., ``+1555667890``)". :func:`validate_phone`
requires a leading plus, a non-zero first digit, and 8 to 15 digits in total. A national
number such as ``(555) 123-4567`` has no country code and is refused, which is the case
the rule exists for: a number that parses as a national number can be silently dialled in
the wrong country.

What an attempt proves
----------------------

The specification says identity is "proven out-of-band (typed passcode, SMS code, KBA
answers, or ID document)" and the result "is stamped onto the recipient's session; the
document body is withheld until it clears". So an attempt takes the evidence out of band
and returns a pass or a fail, and nothing here decides who is holding the phone.
:data:`~dsr.identity_verification.vocabulary.NOT_PROOF` is carried on every answer
because a pass records that the right answer was given, and that is the whole of what it
records.

What this module will not do
----------------------------

It will not remember. The specification's automation section is explicit: "Verification is
re-asserted by the gate on every attempt; there is no 'verify once, remember forever'
behaviour in these flows." :func:`evaluate` takes no session, reads no stored result and
caches nothing: every call re-runs the whole check against the evidence in front of it.
The session stamp lives in :mod:`dsr.identity_verification.engine`, and it records the
last outcome rather than granting a standing permission, which is what makes the second
attempt on the same document run the check again.
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from dsr.identity_verification import vocabulary as vocab

#: The stored shape of one gate's settings. A discriminated union: exactly one method is
#: active, and which one is decided by which config key is present rather than by a
#: separate field that could disagree with the payload.
#:
#: ``{"method": "passcode", "passcode": "Ship2026"}`` and
#: ``{"method": "sms", "phone_number": "+1555667890", "sms_phone_number_type": "both"}``
#: are both valid. ``{"method": "kba", "passcode": "Ship2026"}`` is not, and
#: :func:`validate_settings` says which half is wrong.
METHOD_KEY = "method"
VERIFICATION_SETTINGS = "verification_settings"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four are declared here and raised by nothing else in the product. That is what makes
# it safe for the HTTP layer to map them: FastAPI accepts exception handlers on the app
# object only, the host attaches them, and the host refuses a second feature registering a
# handler for a type this one already claimed.


ROOM_REF = vocab.ROOM_REF


def room_ref_of(data: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> Any:
    """The room a payload belongs to: the payload's own key, else the envelope's.

    Every response projects this back to ``room_id``, so a caller sees one name for one
    thing even though the store keeps the payload-side twin and the envelope's field in
    two different places.
    """

    value = data.get(ROOM_REF)
    if value:
        return value
    return (record or {}).get("room_id")


class VerificationSettingsInvalid(ValueError):
    """A verification setting this workflow will not accept.

    Carries a field-keyed map, because a sender filling in a form needs the message next
    to the input that caused it and not one combined sentence. The HTTP layer renders it
    as ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class RecipientNotFound(LookupError):
    """No such recipient, or it was never a recipient this workflow owns.

    Its own type rather than the store's ``RecordNotFound``, because a feature may only
    map error types it raises itself: registering a handler for a shared type would
    intercept that exception across the whole product.
    """


class DocumentNotFound(LookupError):
    """No such document. Same reasoning as :class:`RecipientNotFound`."""


class GateNotCleared(PermissionError):
    """A caller reached for a document body while a gate on it was still uncleared.

    Carries the gate and the method, because "access denied" tells a recipient nothing
    about what to do next and this workflow's job is to say which check is outstanding.
    """

    def __init__(
        self,
        message: str,
        *,
        place: str | None = None,
        method: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.place = place
        self.method = method
        self.reason = reason


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """An ISO 8601 instant, always in UTC.

    Milliseconds are kept. Two verification attempts inside one second are two attempts a
    reviewer needs to tell apart, and a stamp that rounded to the second would collapse
    them.
    """

    return (moment or utcnow()).astimezone(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Reading a method
# --------------------------------------------------------------------------- #


def normalise_method(value: Any) -> str | None:
    """The method a name refers to, or ``None`` if it names none.

    Both the room's own ids and the vendor's field names resolve, because the method
    table in the specification is written in the vendor's spelling
    (``passcode_verification``) while the workflow talks about a "passcode". Accepting
    one and not the other would force a caller to translate before it could call this API.
    """

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in vocab.METHODS:
        return text
    for method, vendor_field in vocab.METHOD_VENDOR_FIELDS.items():
        if text == vendor_field:
            return method
    aliases = {
        "phone": vocab.METHOD_SMS,
        "sms_otp": vocab.METHOD_SMS,
        "one_time_password": vocab.METHOD_SMS,
        "otp": vocab.METHOD_SMS,
        "knowledge_based": vocab.METHOD_KBA,
        "knowledge_based_authentication": vocab.METHOD_KBA,
        "id_check": vocab.METHOD_ID,
        "id_verification": vocab.METHOD_ID,
        "passcode_verification": vocab.METHOD_PASSCODE,
    }
    return aliases.get(text)


def method_of(entry: Mapping[str, Any]) -> str | None:
    """Which method a stored gate entry describes.

    Read from the config key rather than from ``method``, so a hand-edited record whose
    ``method`` field disagrees with its payload resolves by what it actually carries. The
    two disagreeing is the defect: a record claiming ``kba`` while holding a passcode is
    not a KBA gate, and reading the payload is the only reading that is true.
    """

    declared = normalise_method(entry.get(METHOD_KEY))
    present = [method for method in vocab.METHODS if entry.get(vocab.METHOD_CONFIG_KEYS[method])]
    if len(present) == 1:
        return present[0]
    if declared is not None:
        return declared
    if present:
        # More than one config key is present. The first in the published order is
        # returned so a caller gets a definite answer; validate_settings is what refuses
        # the entry, with a message naming the conflict.
        return present[0]
    return None


def method_config(entry: Mapping[str, Any], method: str | None = None) -> Any:
    """The configuration payload for a gate entry's method."""

    chosen = method or method_of(entry)
    if chosen is None:
        return None
    return entry.get(vocab.METHOD_CONFIG_KEYS[chosen])


def sms_type_of(entry: Mapping[str, Any]) -> str:
    """How an SMS number is used, defaulting to authentication-only.

    The default is ``authentication`` and not ``delivery`` because the gate is an
    authentication gate: a sender who configures an SMS verification has asked for the
    number to be an authentication factor, and a record that omits the field should mean
    the ordinary case rather than the one where the number is not a factor at all.
    """

    raw = str(entry.get(vocab.SMS_TYPE_FIELD) or "").strip().lower()
    return raw if raw in vocab.SMS_TYPES else vocab.SMS_TYPE_AUTHENTICATION


# --------------------------------------------------------------------------- #
# The bounds
# --------------------------------------------------------------------------- #


def validate_passcode(
    value: Any, field: str = vocab.METHOD_CONFIG_KEYS[vocab.METHOD_PASSCODE]
) -> str:
    """A passcode the specification's own rule accepts, or a refusal.

    The rule, quoted: "It must be 6-100 characters with at least one letter and one digit."
    Both bounds are enforced, and the letter and the digit are separate findings, because
    a sender who typed twelve letters needs to be told that rather than told it was too
    short.

    Whitespace is not length. A passcode of six spaces satisfies the length rule and
    nothing else, and a recipient can type six spaces. :func:`passcode_is_provable` is
    where an attempt finds that out; here it is refused at configuration time, because a
    sender who set one should learn immediately rather than at the recipient's first
    attempt.
    """

    if value is None:
        raise VerificationSettingsInvalid(
            "A passcode verification needs a passcode.", {field: vocab.PASSCODE_RULE_TEXT}
        )
    if not isinstance(value, str):
        raise VerificationSettingsInvalid(
            "A passcode must be text.", {field: vocab.PASSCODE_RULE_TEXT}
        )

    text = value
    problems: dict[str, str] = {}
    if not (vocab.PASSCODE_MIN_LENGTH <= len(text) <= vocab.PASSCODE_MAX_LENGTH):
        problems[field] = (
            f"Use {vocab.PASSCODE_MIN_LENGTH} to {vocab.PASSCODE_MAX_LENGTH} characters. "
            f"This one is {len(text)}."
        )
    if vocab.PASSCODE_REQUIRES_LETTER and not any(character.isalpha() for character in text):
        problems[field] = "Include at least one letter."
    if vocab.PASSCODE_REQUIRES_DIGIT and not any(character.isdigit() for character in text):
        problems[field] = "Include at least one digit."
    if not text.strip():
        problems[field] = "A passcode cannot be only spaces."

    if problems:
        raise VerificationSettingsInvalid(
            "This passcode is not one the specification accepts.", problems
        )
    return text


def validate_phone(value: Any, field: str = vocab.METHOD_CONFIG_KEYS[vocab.METHOD_SMS]) -> str:
    """A phone number in international format, or a refusal.

    The rule, quoted: "must be in international format (e.g., ``+1555667890``)". The
    example is E.164 - a plus, a non-zero country digit, then up to fourteen more - and
    :func:`is_e164` implements exactly that.

    A national number is refused rather than repaired. ``(555) 123-4567`` has no country
    code, and a code sent to a number with no country code goes to whichever country the
    dialler is configured for, which is exactly the failure this rule exists to prevent.
    Stripping punctuation and prefixing a default country code would also be inventing a
    fact the sender never stated.
    """

    if value is None:
        raise VerificationSettingsInvalid(
            "A phone verification needs a phone number.", {field: vocab.PHONE_RULE_TEXT}
        )
    if not isinstance(value, str):
        raise VerificationSettingsInvalid(
            "A phone number must be text.", {field: vocab.PHONE_RULE_TEXT}
        )

    text = value.strip()
    if not is_e164(text):
        raise VerificationSettingsInvalid(
            "This phone number is not in international format.", {field: vocab.PHONE_RULE_TEXT}
        )
    return text


def is_e164(value: str) -> bool:
    """Whether ``value`` is a plus, a non-zero country digit, and 8 to 15 digits.

    Read from the specification's own example rather than from the full E.164 standard:
    the example is ``+1555667890``, so the shape the source documents is a plus and a
    country code and a subscriber number. DERIVED_E164_PRECISE records why this build
    reads it that way and what a stricter reading would have cost.
    """

    if not value.startswith(vocab.PHONE_PREFIX):
        return False
    digits = value[1:]
    if not digits.isdigit():
        return False
    if digits[0] == "0":
        return False
    return vocab.PHONE_MIN_DIGITS <= len(digits) <= vocab.PHONE_MAX_DIGITS


def passcode_is_provable(value: Any) -> bool:
    """Whether what a recipient typed could be a real answer.

    Whitespace is not length, so this is not "does it meet the bound". A recipient who
    typed six spaces satisfies the length rule and has proved nothing, and a gate that
    accepted that would be a gate anybody walks through.
    """

    return isinstance(value, str) and bool(value.strip())


def generate_sms_code() -> str:
    """A fresh six-digit one-time code.

    Six digits because both cited sources say six: "a unique SMS authentication code"
    and "a 6-digit code via text message". Generated with :mod:`secrets`, not with
    :mod:`random`, because a code an attacker could predict from an earlier one is not an
    authentication factor. This is the one place the workflow generates anything, and it
    generates a code rather than a question, because the KBA source is inferred and this
    room does not fabricate public records.
    """

    alphabet = string.digits
    return "".join(secrets.choice(alphabet) for _ in range(vocab.SMS_CODE_DIGITS))


# --------------------------------------------------------------------------- #
# Building and validating a recipient's settings
# --------------------------------------------------------------------------- #


def build_settings(
    place: Any,
    method: Any,
    config: Any = None,
    *,
    role: str = vocab.ROLE_RECIPIENT,
    sms_type: Any = None,
) -> dict[str, Any]:
    """One gate's settings, shaped and validated.

    Returns the stored form: the method's id, the config key that method reads, and the
    SMS role when the method is the SMS one. A caller that passes a national phone number
    or a five-character passcode gets a :class:`VerificationSettingsInvalid` here rather
    than a stored setting that fails at the recipient's first attempt.
    """

    key = normalise_place(place)
    if key is None:
        raise VerificationSettingsInvalid(
            "Unknown verification place.",
            {
                "verification_place": f"Use {vocab.PLACE_LABELS[vocab.BEFORE_OPEN]} or "
                f"{vocab.PLACE_LABELS[vocab.BEFORE_SIGN]}."
            },
        )

    resolved = normalise_method(method)
    if resolved is None:
        raise VerificationSettingsInvalid(
            "Unknown verification method.",
            {"method": f"Use one of {', '.join(vocab.METHOD_LABELS[m] for m in vocab.METHODS)}."},
        )

    if not place_applies_to_role(key, role):
        raise VerificationSettingsInvalid(
            "That gate applies to signers only, and this recipient is not a signer.",
            {
                "role": "Set the recipient's role to signer, or verify before open instead.",
                "verification_place": vocab.PLACE_DESCRIPTIONS[key],
            },
        )

    entry: dict[str, Any] = {METHOD_KEY: resolved}

    if resolved == vocab.METHOD_PASSCODE:
        entry[vocab.METHOD_CONFIG_KEYS[resolved]] = validate_passcode(config)
    elif resolved == vocab.METHOD_SMS:
        entry[vocab.METHOD_CONFIG_KEYS[resolved]] = validate_phone(config)
        declared = str(sms_type or "").strip().lower()
        entry[vocab.SMS_TYPE_FIELD] = (
            declared if declared in vocab.SMS_TYPES else vocab.SMS_TYPE_AUTHENTICATION
        )
    elif resolved == vocab.METHOD_KBA:
        entry[vocab.METHOD_CONFIG_KEYS[resolved]] = validate_questions(config)
    else:
        entry[vocab.METHOD_CONFIG_KEYS[resolved]] = validate_id_document(config)

    validate_settings(entry)
    return entry


def normalise_place(value: Any) -> str | None:
    """The gate a name refers to, or ``None``."""

    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "before_viewing": vocab.BEFORE_OPEN,
        "before_opening": vocab.BEFORE_OPEN,
        "open": vocab.BEFORE_OPEN,
        "before_signing": vocab.BEFORE_SIGN,
        "sign": vocab.BEFORE_SIGN,
    }
    if text in vocab.PLACES:
        return text
    return aliases.get(text)


def place_applies_to_role(place: str, role: str) -> bool:
    """Whether this gate applies to a recipient in this role.

    The audience column of the specification's timing table: ``before_open`` is "All
    recipients" and ``before_sign`` is "Signers only". A role this workflow does not know
    is treated as not-a-signer, because defaulting an unknown role to the wider audience
    would let a misspelled role quietly install a gate on somebody who cannot clear it.
    """

    required = vocab.PLACE_REQUIRED_ROLE.get(place)
    if required is None:
        return True
    return str(role or "").strip().lower() == required


def validate_questions(
    value: Any, field: str = vocab.METHOD_CONFIG_KEYS[vocab.METHOD_KBA]
) -> list[dict[str, str]]:
    """Knowledge-based questions with their expected answers, or a refusal.

    At least one question and no more than ten. The upper bound is derived rather than
    sourced - the specification says "identity questions generated from public records"
    and names no count - and DERIVED_KBA_QUESTION_COUNT records it.

    Each entry carries a prompt and an answer, because a question with no expected answer
    is not a question this workflow can grade.
    """

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise VerificationSettingsInvalid(
            "Knowledge-based verification needs a list of questions.",
            {field: "Record each question with the answer you expect."},
        )

    questions: list[dict[str, str]] = []
    problems: dict[str, str] = {}
    for index, item in enumerate(value):
        label = f"{field}[{index}]"
        if not isinstance(item, Mapping):
            problems[label] = "Record each question as an object with a prompt and an answer."
            continue
        prompt = str(item.get(vocab.KBA_PROMPT_FIELD) or "").strip()
        answer = str(item.get(vocab.KBA_ANSWER_FIELD) or "").strip()
        if not prompt:
            problems[label] = "Give the question a prompt."
            continue
        if not answer:
            problems[label] = "Record the answer you expect, or this question cannot be graded."
            continue
        questions.append({vocab.KBA_PROMPT_FIELD: prompt, vocab.KBA_ANSWER_FIELD: answer})

    if not problems and len(questions) < vocab.KBA_MIN_QUESTIONS:
        problems[field] = f"Record at least {vocab.KBA_MIN_QUESTIONS} question."
    if len(questions) > vocab.KBA_MAX_QUESTIONS:
        problems[field] = f"Record at most {vocab.KBA_MAX_QUESTIONS} questions."

    if problems:
        raise VerificationSettingsInvalid(
            "These knowledge-based questions are not ones this workflow can grade.", problems
        )
    return questions


def validate_id_document(
    value: Any, field: str = vocab.METHOD_CONFIG_KEYS[vocab.METHOD_ID]
) -> dict[str, str]:
    """The ID details a recipient will be asked for, or a refusal.

    The evidence says the recipient "verifies identity with a government-issued ID" and
    names no provider, so this build checks the details the sender recorded against the
    details the recipient stated. It is not a document check and nothing here claims to
    read a document; DERIVED_ID_PROVIDER records that.
    """

    if not isinstance(value, Mapping):
        raise VerificationSettingsInvalid(
            "An ID verification needs the details the recipient will be asked for.",
            {field: "Record at least a document type and a document number."},
        )

    problems: dict[str, str] = {}
    details: dict[str, str] = {}
    for name in vocab.ID_DOCUMENT_FIELDS:
        text = str(value.get(name) or "").strip()
        if not text:
            # An optional field the sender left out is skipped, not refused. Refusing it
            # would make a sender invent a country to satisfy a form.
            if name in vocab.ID_DOCUMENT_REQUIRED_FIELDS:
                problems[f"{field}.{name}"] = "Record this, or remove the ID check."
            continue
        details[name] = text

    if not details.get("document_type"):
        problems[f"{field}.document_type"] = "Record the type of ID, for example passport."
    if not details.get("document_number"):
        problems[f"{field}.document_number"] = "Record the document number you expect."

    if problems:
        raise VerificationSettingsInvalid(
            "These ID details are not ones this workflow can check.", problems
        )
    return details


def validate_settings(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A gate entry whose method and payload agree, or a refusal.

    The check a discriminated union needs: exactly one config key is present, and it is
    the one the method reads. An entry holding both a passcode and a list of questions
    names a method and carries the other's payload, and the two halves would disagree
    about what a recipient is being asked.

    The audience check is not repeated here. It belongs to
    :func:`apply_settings`, which is the only caller that knows which gate the entry is
    for, and re-deriving it here would mean this function had to guess.
    """

    declared = normalise_method(entry.get(METHOD_KEY))
    present = [
        method
        for method in vocab.METHODS
        if entry.get(vocab.METHOD_CONFIG_KEYS[method]) not in (None, "", [], {})
    ]
    problems: dict[str, str] = {}

    if declared is None:
        problems[METHOD_KEY] = (
            f"Use one of {', '.join(vocab.METHOD_LABELS[m] for m in vocab.METHODS)}."
        )
    if len(present) > 1:
        named = ", ".join(vocab.METHOD_LABELS[method] for method in present)
        problems[METHOD_KEY] = f"This entry carries more than one method: {named}. Use one."
    elif declared is not None and present and present[0] != declared:
        problems[METHOD_KEY] = (
            f"This entry says {vocab.METHOD_LABELS[declared]} but carries "
            f"{vocab.METHOD_LABELS[present[0]]}."
        )
    elif declared is not None and not present:
        problems[vocab.METHOD_CONFIG_KEYS[declared]] = (
            f"{vocab.METHOD_LABELS[declared]} needs its own setting."
        )

    if problems:
        raise VerificationSettingsInvalid(
            "This verification setting does not describe one method.", problems
        )

    resolved = present[0] if present else declared
    assert resolved is not None  # the branches above leave exactly one resolved method

    if resolved == vocab.METHOD_PASSCODE:
        validate_passcode(entry.get(vocab.METHOD_CONFIG_KEYS[resolved]))
    elif resolved == vocab.METHOD_SMS:
        validate_phone(entry.get(vocab.METHOD_CONFIG_KEYS[resolved]))
        declared_type = str(entry.get(vocab.SMS_TYPE_FIELD) or "").strip().lower()
        if declared_type and declared_type not in vocab.SMS_TYPES:
            problems[vocab.SMS_TYPE_FIELD] = f"Use {', '.join(vocab.SMS_TYPES)}."
    elif resolved == vocab.METHOD_KBA:
        validate_questions(entry.get(vocab.METHOD_CONFIG_KEYS[resolved]))
    else:
        validate_id_document(entry.get(vocab.METHOD_CONFIG_KEYS[resolved]))

    if problems:
        raise VerificationSettingsInvalid(
            "This verification setting does not describe one method.", problems
        )
    return dict(entry)


def apply_settings(
    current: Mapping[str, Any], changes: Mapping[str, Any], *, role: str = vocab.ROLE_RECIPIENT
) -> dict[str, Any]:
    """The recipient's gates after applying ``changes`` on top of ``current``.

    Three states a gate can be in, and each has its own answer:

    * ``changes`` omits the gate. What is stored stands. That is what "add/change
      ``verification_settings`` on an existing document" means on a patch, and it is how a
      recipient verified before open can later gain a different gate before sign without
      the first one being disturbed.
    * ``changes`` sets the gate. That entry replaces what was stored for that gate, after
      :func:`build_settings` has shaped and validated it. Replacing rather than merging is
      the correct reading of a discriminated union: a gate has exactly one method, so a
      sender who changes the method is not adding to it.
    * ``changes`` sets the gate to ``null``. It removes the gate, which is how a
      verification is taken off a recipient on a live document.

    The returned mapping is keyed by gate, which is what lets one recipient carry a
    passcode before open and a knowledge-based check before sign at the same time.
    """

    stored = dict(current or {})
    for place, value in (changes or {}).items():
        key = normalise_place(place)
        if key is None:
            raise VerificationSettingsInvalid(
                "Unknown verification place.",
                {
                    str(place): f"Use {vocab.PLACE_LABELS[vocab.BEFORE_OPEN]} or "
                    f"{vocab.PLACE_LABELS[vocab.BEFORE_SIGN]}."
                },
            )
        if value is None:
            stored.pop(key, None)
            continue
        if not isinstance(value, Mapping):
            raise VerificationSettingsInvalid(
                "A verification setting must be an object.",
                {key: "Give the method and the value it checks."},
            )
        method = normalise_method(value.get(METHOD_KEY))
        if method is None:
            # No method named: infer it from whichever config key the caller supplied, so
            # a sender who posts {"passcode": "Ship2026"} is not asked to also name the
            # method they just named.
            method = next(
                (
                    candidate
                    for candidate in vocab.METHODS
                    if value.get(vocab.METHOD_CONFIG_KEYS[candidate]) not in (None, "", [], {})
                ),
                None,
            )
        if method is None:
            raise VerificationSettingsInvalid(
                "This verification setting names no method.",
                {key: f"Use one of {', '.join(vocab.METHOD_LABELS[m] for m in vocab.METHODS)}."},
            )
        stored[key] = build_settings(
            key,
            method,
            value.get(vocab.METHOD_CONFIG_KEYS[method]),
            role=role,
            sms_type=value.get(vocab.SMS_TYPE_FIELD),
        )
    return stored


def settings_for(data: Mapping[str, Any], place: Any) -> dict[str, Any] | None:
    """The gate entry a recipient carries at ``place``, or ``None``.

    A recipient with no entry at a gate is not blocked at that gate. That is the ordinary
    case and it is not an error: most recipients in a room carry no verification
    settings at all, and refusing to read them would be a gate on everything.
    """

    key = normalise_place(place)
    if key is None:
        return None
    entry = (data.get(VERIFICATION_SETTINGS) or {}).get(key)
    return dict(entry) if isinstance(entry, Mapping) else None


def gates_of(data: Mapping[str, Any]) -> list[str]:
    """Which gates a recipient carries, in the specification's order."""

    stored = data.get(VERIFICATION_SETTINGS) or {}
    return [place for place in vocab.PLACES if isinstance(stored, Mapping) and place in stored]


def settings_summary(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A recipient's gates as the page renders them.

    Each entry names the method, the vendor's own field name for it, and whether the SMS
    role means the number is an authentication factor, a delivery channel, or both. That
    last one is the axis the specification's extensibility note singles out, and a page
    that cannot show it cannot show the difference between a number that proves identity
    and a number that only carries the document.
    """

    summary = []
    for place in gates_of(data):
        entry = settings_for(data, place) or {}
        method = method_of(entry)
        summary.append(
            {
                "place": place,
                "label": vocab.PLACE_LABELS[place],
                "description": vocab.PLACE_DESCRIPTIONS[place],
                "audience": vocab.PLACE_AUDIENCE[place],
                "method": method,
                "method_label": vocab.METHOD_LABELS.get(method or "", "Unknown method"),
                "vendor_field": vocab.METHOD_VENDOR_FIELDS.get(method or "", ""),
                "sms_type": sms_type_of(entry) if method == vocab.METHOD_SMS else None,
                "sms_type_meaning": (
                    vocab.SMS_TYPE_MEANINGS[sms_type_of(entry)]
                    if method == vocab.METHOD_SMS
                    else None
                ),
                "needs_delivery": bool(vocab.METHOD_NEEDS_DELIVERY.get(method or "", False)),
            }
        )
    return summary


def is_authentication_factor(data: Mapping[str, Any], place: Any) -> bool:
    """Whether a gate uses a number as an authentication factor rather than for delivery.

    The specification records that an SMS "can be an auth factor, a delivery channel, or
    both", so the three states are three answers rather than a boolean. Only
    ``authentication`` and ``both`` make the number a factor; ``delivery`` means the code
    goes out but proves nothing on its own, and the page says so rather than counting it
    as verification.
    """

    entry = settings_for(data, place)
    if entry is None or method_of(entry) != vocab.METHOD_SMS:
        return False
    return sms_type_of(entry) in (vocab.SMS_TYPE_AUTHENTICATION, vocab.SMS_TYPE_BOTH)


# --------------------------------------------------------------------------- #
# Evaluating one attempt
# --------------------------------------------------------------------------- #


def evaluate(
    recipient: Mapping[str, Any],
    place: Any,
    evidence: Mapping[str, Any] | None = None,
    *,
    delivered_code: str | None = None,
) -> dict[str, Any]:
    """Run one verification attempt and return what happened.

    Never raises for a wrong answer. A failed attempt is a normal, recorded outcome, not
    an exception: the specification's requirement is that "a rejected attempt is as
    visible as a successful one", and an exception that unwinds past the write is the
    opposite of visible. It raises only for a caller who asked about a gate this workflow
    does not recognise.

    Nothing here reads a stored result and nothing is cached. The specification's
    automation section says verification "is re-asserted by the gate on every attempt;
    there is no 'verify once, remember forever' behaviour", so this function re-runs the
    whole check from the recipient's stored settings and the evidence in front of it every
    time it is called. The second attempt on a document is not answered from the first.

    ``delivered_code`` is the one-time code the room sent to this recipient, when the
    method is the SMS one. It is passed in rather than read from a store so that the
    evaluation stays a pure function of its arguments, and so the code cannot be read out
    of a record by anything that does not need it.
    """

    key = normalise_place(place)
    if key is None:
        raise VerificationSettingsInvalid(
            "Unknown verification place.",
            {
                "verification_place": f"Use {vocab.PLACE_LABELS[vocab.BEFORE_OPEN]} or "
                f"{vocab.PLACE_LABELS[vocab.BEFORE_SIGN]}."
            },
        )

    role = str(recipient.get("role") or vocab.ROLE_RECIPIENT).strip().lower()
    answer: dict[str, Any] = {
        "place": key,
        "method": None,
        "outcome": vocab.OUTCOME_FAIL,
        "reason": vocab.REASON_NO_CONFIG,
        "role": role,
    }

    entry = settings_for(recipient, key)
    if entry is None:
        # No gate here is not a failure. It is the ordinary case for most recipients, and
        # the caller decides what an ungated document means for it.
        answer["gated"] = False
        answer["outcome"] = None
        answer["reason"] = None
        return answer

    answer["gated"] = True

    if not place_applies_to_role(key, role):
        # The gate's audience does not include this recipient. Reported as a failed
        # attempt with the reason named, rather than silently passing: a before_sign gate
        # that quietly let a non-signer through would be the worse defect of the two.
        answer["reason"] = vocab.REASON_NOT_A_SIGNER
        return answer

    method = method_of(entry)
    answer["method"] = method
    if method is None:
        answer["reason"] = vocab.REASON_NO_CONFIG
        return answer

    supplied = dict(evidence or {})
    verdict = _check(method, entry, supplied, delivered_code)
    answer["outcome"] = verdict["outcome"]
    answer["reason"] = verdict["reason"]
    answer["sms_type"] = sms_type_of(entry) if method == vocab.METHOD_SMS else None
    return answer


def _check(
    method: str, entry: Mapping[str, Any], supplied: Mapping[str, Any], delivered_code: str | None
) -> dict[str, Any]:
    """Compare one method's evidence against its stored setting.

    Split per method so each comparison reads as the rule it is, and so adding a fifth
    method is one function rather than a branch that has to be found inside a large one.

    Every comparison is constant-time where the operand is a secret. A passcode compared
    with ``==`` leaks its prefix through timing; :func:`secrets.compare_digest` does not.
    That is the difference between a gate and a decoration.
    """

    if method == vocab.METHOD_PASSCODE:
        expected = str(entry.get(vocab.METHOD_CONFIG_KEYS[method]) or "")
        offered = supplied.get(vocab.METHOD_CONFIG_KEYS[method])
        if not passcode_is_provable(offered):
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_MALFORMED_PASSCODE}
        if not secrets.compare_digest(str(offered), expected):
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_WRONG_PASSCODE}
        return {"outcome": vocab.OUTCOME_PASS, "reason": None}

    if method == vocab.METHOD_SMS:
        offered = supplied.get("code")
        if offered is None and vocab.METHOD_CONFIG_KEYS[method] in supplied:
            offered = supplied.get(vocab.METHOD_CONFIG_KEYS[method])
        if delivered_code is None:
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_NO_CODE_SENT}
        if offered is None or not str(offered).strip():
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_WRONG_CODE}
        if not secrets.compare_digest(str(offered).strip(), str(delivered_code).strip()):
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_WRONG_CODE}
        return {"outcome": vocab.OUTCOME_PASS, "reason": None}

    if method == vocab.METHOD_KBA:
        expected = entry.get(vocab.METHOD_CONFIG_KEYS[method]) or []
        answers = supplied.get("answers")
        if not isinstance(answers, Mapping):
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_ANSWERS_INCOMPLETE}
        prompts = {str(question.get(vocab.KBA_PROMPT_FIELD)): question for question in expected}
        for prompt, question in prompts.items():
            offered = answers.get(prompt)
            if offered is None:
                return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_ANSWERS_INCOMPLETE}
            if not secrets.compare_digest(
                str(offered).strip().lower(), str(question[vocab.KBA_ANSWER_FIELD]).strip().lower()
            ):
                return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_ANSWER_MISMATCH}
        return {"outcome": vocab.OUTCOME_PASS, "reason": None}

    if method == vocab.METHOD_ID:
        expected = entry.get(vocab.METHOD_CONFIG_KEYS[method]) or {}
        offered = supplied.get(vocab.METHOD_CONFIG_KEYS[method])
        if not isinstance(offered, Mapping):
            return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_ID_MISMATCH}
        for name, value in expected.items():
            given = str(offered.get(name) or "").strip().lower()
            if not secrets.compare_digest(given, str(value).strip().lower()):
                return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_ID_MISMATCH}
        return {"outcome": vocab.OUTCOME_PASS, "reason": None}

    return {"outcome": vocab.OUTCOME_FAIL, "reason": vocab.REASON_NO_CONFIG}


def prompt_for(entry: Mapping[str, Any]) -> dict[str, Any]:
    """What a recipient is asked for at this gate, and never the answer.

    The questions come back for a knowledge-based gate because a recipient cannot answer
    a question they cannot see. The expected answers do not, for any method, and neither
    does a passcode or a phone number beyond what the recipient already gave. A prompt
    endpoint that echoed the expected passcode back would be a gate with no gate in it.
    """

    method = method_of(entry) or ""
    prompt: dict[str, Any] = {
        "method": method,
        "method_label": vocab.METHOD_LABELS.get(method, "Unknown method"),
        "description": vocab.METHOD_DESCRIPTIONS.get(method, ""),
    }

    if method == vocab.METHOD_PASSCODE:
        prompt["asks"] = "passcode"
        prompt["min_length"] = vocab.PASSCODE_MIN_LENGTH
        prompt["max_length"] = vocab.PASSCODE_MAX_LENGTH
    elif method == vocab.METHOD_SMS:
        prompt["asks"] = "code"
        prompt["code_digits"] = vocab.SMS_CODE_DIGITS
        prompt["can_request_another_code"] = vocab.SMS_RESEND_ALLOWED
        prompt["sms_type"] = sms_type_of(entry)
        prompt["sms_type_meaning"] = vocab.SMS_TYPE_MEANINGS[sms_type_of(entry)]
    elif method == vocab.METHOD_KBA:
        questions = entry.get(vocab.METHOD_CONFIG_KEYS[method]) or []
        prompt["asks"] = "answers"
        prompt["questions"] = [
            {vocab.KBA_PROMPT_FIELD: question.get(vocab.KBA_PROMPT_FIELD)} for question in questions
        ]
        prompt["question_count"] = len(questions)
    elif method == vocab.METHOD_ID:
        expected = entry.get(vocab.METHOD_CONFIG_KEYS[method]) or {}
        prompt["asks"] = "id_document"
        prompt["fields"] = [name for name in vocab.ID_DOCUMENT_FIELDS if name in expected]
    return prompt
