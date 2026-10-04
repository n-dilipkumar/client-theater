"""The consent profile: step 1 through step 5 of the researched flow.

The research locates every one of these switches on one Admin centre page,
*Data capture > Recording consent*, and step 3 states the rule that decides what
happens when two of them disagree: "These compliance settings are applied to all
web conference providers you add to the consent profile." So the profile owns
the switches, and a provider only chooses *which conference*.

Nothing here touches a database. A profile is validated into a normalised dict
that the engine stores, which is what lets the rules be tested without a request
and what keeps a schema-flexible payload from becoming an unvalidated dict.
"""

from __future__ import annotations

from typing import Any

from dsr.recording_consent import vocabulary as vocab
from dsr.recording_consent.errors import ProfileInvalid

#: The switches step 2 turns on. Both are booleans with an explicit default,
#: because "absent" and "off" must mean the same thing here: an administrator who
#: never opened the recording settings has not enabled anything.
BOOLEAN_SWITCHES: tuple[str, ...] = (
    vocab.CONSENT_PAGE_SWITCH,
    vocab.ENFORCEMENT_SWITCH,
    vocab.JOIN_WITHOUT_CONSENT_SWITCH,
    vocab.SUPPRESS_PROMPT_WHEN_CONSENT_PAGE_USED,
    vocab.PRECALL_EMAIL_SWITCH,
    vocab.AUDIO_PROMPT_SWITCH,
)

#: Re-exported so a caller that is reading this module's settings does not have to
#: reach into the vocabulary table for one field name.
PRECALL_EMAIL_SWITCH = vocab.PRECALL_EMAIL_SWITCH
AUDIO_PROMPT_SWITCH = vocab.AUDIO_PROMPT_SWITCH


def _as_bool(value: Any, field: str, errors: dict[str, str]) -> bool:
    if isinstance(value, bool):
        return value
    # The store is schema-flexible and a JSON client may send 0/1 or "true".
    # Accepting the three spellings a JSON body realistically carries is cheaper
    # than refusing a value that means exactly what the caller meant.
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    errors[field] = "must be true or false"
    return False


def _as_text(value: Any, errors: dict[str, str], field: str, *, required: bool = True) -> str:
    if value is None:
        if required:
            errors[field] = "is required"
        return ""
    if not isinstance(value, str):
        errors[field] = "must be text"
        return ""
    text = value.strip()
    if required and not text:
        errors[field] = "must not be empty"
    return text


def validate_providers(raw: Any, errors: dict[str, str]) -> dict[str, str]:
    """Normalise the per-provider link settings from step 3.

    The research offers exactly three link kinds per provider and four providers.
    Anything else is refused rather than dropped: a provider added with a link
    kind this workflow does not implement would mint a link whose consent page
    never appears, which is the failure the whole workflow exists to prevent.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors["providers"] = "must be an object keyed by provider"
        return {}

    result: dict[str, str] = {}
    for key, value in raw.items():
        slug = str(key).strip().lower()
        if slug not in vocab.PROVIDERS:
            errors[f"providers.{slug}"] = (
                f"unknown provider; the research names {', '.join(sorted(vocab.PROVIDERS))}"
            )
            continue
        # A provider maps to a link kind. Accept the bare string and the
        # one-key object, because a form posts the first and a config file
        # carries the second.
        kind = value.get("link_kind") if isinstance(value, dict) else value
        kind = str(kind or "").strip().lower()
        if kind not in vocab.LINK_KINDS:
            errors[f"providers.{slug}"] = f"link_kind must be one of {', '.join(vocab.LINK_KINDS)}"
            continue
        result[slug] = kind
    return result


def validate_precall_email(raw: Any, errors: dict[str, str], *, enabled: bool) -> dict[str, Any]:
    """Normalise step 4's pre-call email.

    Every ``{{token}}`` in the subject or body must be one the research names.
    An unknown token is an error rather than a literal, because the failure mode
    of accepting one is a consent disclosure that reaches a buyer with
    ``{{unknown}}`` printed inside it.

    ``enabled`` is passed rather than read from the payload so the caller owns the
    one decision that matters here: a profile with the pre-call email switched off
    legitimately holds an empty subject, and demanding one anyway would make every
    profile that does not use the feature invalid - including the result of
    patching a profile that does.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        errors["precall_email"] = "must be an object"
        return {}

    result: dict[str, Any] = {}
    for field in vocab.PRECALL_EMAIL_TEXT_FIELDS:
        result[field] = _as_text(raw.get(field), errors, f"precall_email.{field}", required=False)

    # The subject is the one field with no non-empty fallback: an email with no
    # subject is not a disclosure a participant can recognise in their inbox.
    if enabled and not result.get("subject"):
        errors["precall_email.subject"] = "is required when the pre-call email is enabled"

    tokens: set[str] = set()
    for field in ("subject", "body"):
        text = str(result.get(field) or "")
        while "{{" in text:
            start = text.index("{{")
            end = text.find("}}", start)
            if end == -1:
                errors[f"precall_email.{field}"] = "has an unclosed {{variable}}"
                break
            tokens.add(text[start : end + 2])
            text = text[end + 2 :]

    unknown = sorted(tokens - set(vocab.PRECALL_EMAIL_VARIABLES))
    for token in unknown:
        errors["precall_email.tokens"] = (
            f"{token} is not a variable the research names: "
            f"{', '.join(sorted(vocab.PRECALL_EMAIL_VARIABLES))}"
        )
    result["variables"] = sorted(tokens)
    return result


def validate_audio_prompt(raw: Any, errors: dict[str, str]) -> dict[str, Any]:
    """Normalise step 5's audio prompt.

    The mode is validated rather than assumed, because the research offers two
    and :data:`vocabulary.DEFAULT_PROMPT_MODE` records which one this workflow
    implements and why. Accepting ``every_guest`` keeps that decision honest: an
    administrator who wants the other behaviour can have it, and the choice is
    visible in the stored profile.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        errors["audio_prompt"] = "must be an object"
        return {}

    mode = str(raw.get("mode") or vocab.DEFAULT_PROMPT_MODE).strip().lower()
    if mode not in vocab.PROMPT_MODES:
        errors["audio_prompt.mode"] = f"must be one of {', '.join(vocab.PROMPT_MODES)}"
        mode = vocab.DEFAULT_PROMPT_MODE

    text = (
        _as_text(raw.get("text"), errors, "audio_prompt.text", required=False)
        or vocab.DEFAULT_PROMPT_TEXT
    )

    return {
        "mode": mode,
        "text": text,
        "suppress_when_consent_page_used": bool(raw.get("suppress_when_consent_page_used", True)),
    }


def normalise(payload: Any) -> dict[str, Any]:
    """Validate a consent profile payload into the dict the engine stores.

    Raises :class:`~dsr.recording_consent.errors.ProfileInvalid` carrying a
    field-keyed map, so the caller can put each message beside its input. The
    function validates everything before raising, rather than stopping at the
    first problem: an administrator filling in one form should see every problem
    with it in one pass.
    """
    if not isinstance(payload, dict):
        raise ProfileInvalid("A consent profile must be an object.", {"body": "must be an object"})

    errors: dict[str, str] = {}
    result: dict[str, Any] = {}

    result["name"] = _as_text(payload.get("name"), errors, "name")
    result["description"] = _as_text(
        payload.get("description"), errors, "description", required=False
    )

    for switch in BOOLEAN_SWITCHES:
        result[switch] = _as_bool(payload.get(switch, False), switch, errors)

    result["providers"] = validate_providers(payload.get("providers"), errors)

    if result[vocab.CONSENT_PAGE_SWITCH] and not result["providers"] and "providers" not in errors:
        # Step 3 is "Admin adds web conference providers", and Gong will not issue a
        # consent meeting without one. A consent page with no provider behind it is a
        # page that would issue a link on the wrong conference, or none at all, so the
        # profile is refused here rather than falling back to a default provider at
        # booking time.
        #
        # Guarded on `"providers" not in errors` so this does not overwrite a more
        # specific complaint. A caller who sent `providers` in the wrong shape is told
        # to fix the shape; replacing that with "add a provider" would send them to fix
        # the wrong thing.
        errors["providers"] = "at least one provider is required when the consent page is on"

    default_provider = payload.get(vocab.DEFAULT_PROVIDER_FIELD)
    if not default_provider and result["providers"]:
        # Step 3's "Set as default provider" is the common case of exactly one provider,
        # so a profile that names none is given its only one. `next(iter(...))` rather
        # than `popitem()`: popping would remove the provider from the profile that
        # very line is choosing it out of, and the default would then name a provider
        # the profile no longer carries.
        default_provider = next(iter(result["providers"]))
    default_provider = str(default_provider or "").strip().lower()
    if default_provider and default_provider not in vocab.PROVIDERS:
        errors[vocab.DEFAULT_PROVIDER_FIELD] = (
            f"must be one of {', '.join(sorted(vocab.PROVIDERS))}"
        )
    elif default_provider and default_provider not in result["providers"]:
        # Step 3's "Set as default provider" only means something for a provider
        # the profile actually carries, and this profile already lost that
        # provider to a link-kind error above. Naming it here is more useful than
        # a second complaint about the same field.
        errors[vocab.DEFAULT_PROVIDER_FIELD] = "must name a provider this profile carries"
    result[vocab.DEFAULT_PROVIDER_FIELD] = default_provider or None

    locales = payload.get("locales")
    if locales is None:
        locales = [vocab.CONSENT_PAGE_LOCALES[0]]
    if not isinstance(locales, list):
        errors["locales"] = "must be a list of language codes"
        locales = []
    bad = sorted({str(x) for x in locales} - set(vocab.CONSENT_PAGE_LOCALES))
    if bad:
        errors["locales"] = (
            f"unsupported language(s) {', '.join(bad)}; "
            f"the research names {', '.join(vocab.CONSENT_PAGE_LOCALES)}"
        )
    result["locales"] = sorted({str(x) for x in locales})

    result["logo_url"] = _as_text(payload.get("logo_url"), errors, "logo_url", required=False)
    result["precall_email"] = validate_precall_email(
        payload.get("precall_email"), errors, enabled=result[PRECALL_EMAIL_SWITCH]
    )
    result["audio_prompt"] = validate_audio_prompt(payload.get("audio_prompt"), errors)
    # Step 6's "the profile is set as default for new team members". A boolean, not
    # a pointer: exactly one profile may carry it, and two would make "which
    # profile does an unassigned user get" unanswerable in the same way two default
    # providers would.
    result["is_default"] = _as_bool(payload.get("is_default", False), "is_default", errors)

    if errors:
        raise ProfileInvalid(
            f"{len(errors)} field(s) are not acceptable: " + "; ".join(sorted(errors)),
            errors,
        )
    return result


def patch(existing: dict[str, Any], changes: Any) -> dict[str, Any]:
    """Merge a partial update into an existing profile and revalidate.

    The merge is a plain shallow update followed by a full revalidation, rather
    than a partial validation of only the fields that changed. The reason is
    cross-field rules: turning the consent page on makes the profile invalid
    unless a provider and a default provider are present, and neither of those
    is in the same request as the switch in the common case. Validating only the
    fields that changed would let the profile through in a state no single
    request asked for.

    The merge is tri-state, because ``PATCH`` has to be able to *clear* a value and
    a merge cannot express that:

    * omit a key to leave it alone;
    * send an object to merge into the stored one, key by key;
    * send ``null`` to replace it outright.

    The third is the only way to empty ``providers``, and a merge would silently
    keep every existing provider instead - so a caller would believe they had
    removed one and had not.
    """
    if not isinstance(changes, dict):
        raise ProfileInvalid("A profile patch must be an object.", {"body": "must be an object"})

    unknown = sorted(set(changes) - set(_patchable_keys()))
    if unknown:
        raise ProfileInvalid(
            f"{len(unknown)} field(s) cannot be patched: {', '.join(unknown)}",
            {field: "is not a patchable field" for field in unknown},
        )

    merged = dict(existing)
    for key, value in changes.items():
        if value is None:
            merged[key] = None
        elif key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return normalise(merged)


def _patchable_keys() -> tuple[str, ...]:
    """Every key a patch may carry.

    Excludes ``name``-adjacent identifiers on purpose: the record id, the room
    and the created stamp are envelope or provenance, and a patch that could
    rewrite them would let a caller move a profile onto another tenant's row.
    """
    return (
        "name",
        "description",
        *BOOLEAN_SWITCHES,
        "providers",
        vocab.DEFAULT_PROVIDER_FIELD,
        "locales",
        "logo_url",
        "precall_email",
        "audio_prompt",
        "is_default",
    )
