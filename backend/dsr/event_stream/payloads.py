"""The ``webhook-event`` payload: what actually goes over the wire.

Sourced shape
-------------
The researched data flow is the whole specification of this module:

    "Buyer action inside the workspace -> Dock emits a ``webhook-event`` with
    ``occurredAt``, ``propertyName``/``propertyPreviousValue``/``propertyValue``,
    and ``associatedObjects`` (``workspace``, ``account``, ``user``, plus
    ``workspacePage`` / ``workspaceSection`` / ``workspacePlanTask`` / ``file`` /
    ``workspaceForm``) -> POST to the subscriber URL"

Plus four rules from the research corpus that change the shape rather than just
listing it:

* **"Anonymous activity omits ``user``."** The key is *absent*, not null. That
  is the single most consequential rule in this module: a subscriber counting
  engagement keys on ``associatedObjects.user`` and must be able to tell "no
  user" from "an empty user". :func:`normalise_associated_objects` implements it
  by omission, and :func:`is_anonymous` is how a reader of a stored event tells
  the two apart.
* **"``presentation.viewed`` and ``presentation.downloaded`` are emitted for
  presentation share-link activity only."** A ``presentation.*`` event with no
  share link is not a presentation event; it is a mistake, and it is refused
  rather than quietly posted.
* **``asset.*`` carries the asset snapshot, including ``trackingEnabled``**,
  "to distinguish gated share links". The snapshot is at the **top level**,
  not in ``associatedObjects``, because ``asset`` is not one of the eight
  researched object kinds.
* **``workspace.form.submitted`` carries ``formQuestions`` +
  ``formQuestionResponses``**, typed, "incl. ``file_upload``".

The envelope
------------
``object``, ``id`` and the three property fields are the researched payload.
``test`` is this build's addition, on both verification POSTs and "Send test
events", so a subscriber can route them away from its warehouse. It is recorded
as inference ``test-events-are-marked``.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.event_stream.errors import EventPayloadError
from dsr.event_stream.targets import expiry_note, is_expired, iso, parse_now
from dsr.event_stream.vocabulary import (
    ALWAYS_ASSOCIATED,
    ASSOCIATED_OBJECTS,
    FILE_UPLOAD_QUESTION,
    OPTIONAL_ASSOCIATED,
    associated_object_url,
    is_asset_event,
    is_form_event,
    is_share_link_event,
    required_objects_for,
    require_event,
)

#: The object name on the wire. Sourced: the payload "emits a ``webhook-event``".
WEBHOOK_EVENT_OBJECT = "webhook-event"

#: The property fields the research names on every payload.
PROPERTY_FIELDS: tuple[str, ...] = ("propertyName", "propertyPreviousValue", "propertyValue")

#: The key carrying a share link on a ``presentation.*`` event. Not a researched
#: name - the research says the activity comes from a share link without naming
#: the field - so it is an inference, ``inferences.py`` entry
#: ``share-link-field-name``.
SHARE_LINK_FIELD = "shareLink"


# --------------------------------------------------------------------------- #
# associatedObjects
# --------------------------------------------------------------------------- #


def normalise_associated_objects(value: Any, *, event: str = "") -> dict[str, dict[str, Any]]:
    """Validate and normalise the ``associatedObjects`` a caller supplied.

    Each entry is a mapping with an ``id``; anything else - a string, a number, a
    list - is refused, because the research's own example is an object with
    ``id``/``object``/``url`` and a bare string would break every subscriber
    that reads ``.id``.

    Enforces the two structural rules:

    * the objects the event type always carries must be present, so a
      ``workspace.page.viewed`` can never reach a subscriber without its
      ``workspacePage``;
    * ``user`` is dropped when it is empty, which is the researched
      "anonymous activity omits ``user``" rule expressed as omission rather
      than as a null.
    """
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise EventPayloadError(
            f"associatedObjects must be a JSON object, got {type(value).__name__}",
            remediation=(
                "Send an object keyed by the researched object kinds, for example "
                '{"workspace": {"id": "ws_1"}, "user": {"id": "u_1", "email": "a@b.example"}}.'
            ),
        )

    unknown = sorted(set(value) - set(ASSOCIATED_OBJECTS))
    if unknown:
        raise EventPayloadError(
            f"unknown associated object kind(s): {', '.join(unknown)}",
            remediation=(
                "The published kinds are "
                f"{', '.join(ASSOCIATED_OBJECTS)}. Extra per-event detail belongs in "
                "metadata rather than pretending to be an object kind."
            ),
        )

    cleaned: dict[str, dict[str, Any]] = {}
    for kind, entry in value.items():
        if entry is None:
            continue
        if isinstance(entry, str):
            # `{"workspace": "ws_1"}` is the shape a hand-written test reaches
            # for first, so it is accepted rather than refused - but it is
            # normalised to the researched object shape on the way through.
            entry = {"id": entry}
        if not isinstance(entry, Mapping):
            raise EventPayloadError(
                f"associatedObjects.{kind} must be an object or an id, got {type(entry).__name__}",
                remediation=f'Send {kind} as {{"id": "..."}} or as a bare id string.',
            )
        # An optional object carrying nothing at all is the *anonymous* case,
        # and the researched rule is that anonymous activity omits `user` - not
        # that it sends a user whose every field is blank. Dropping it here means
        # the rule holds for every call path, not only the ones that remember to
        # check. A required object in the same shape is a mistake and is refused.
        if kind in OPTIONAL_ASSOCIATED and not any(
            str(part).strip() for part in entry.values() if part is not None
        ):
            continue
        object_id = str(entry.get("id") or "").strip()
        if not object_id:
            raise EventPayloadError(
                f"associatedObjects.{kind} needs an id",
                remediation=f"Every associated object carries an id; {kind} did not.",
            )
        cleaned[kind] = {"id": object_id, **{k: v for k, v in entry.items() if k != "id"}}

    if not cleaned:
        raise EventPayloadError(
            "associatedObjects is empty",
            remediation=f"An event always carries at least {', '.join(ALWAYS_ASSOCIATED)}.",
        )

    if event:
        missing = [kind for kind in required_objects_for(event) if kind not in cleaned]
        if missing:
            raise EventPayloadError(
                f"{event} must carry associated object(s) {', '.join(missing)}",
                code="missing_associated_object",
                remediation=(
                    f"The {event} event names {', '.join(missing)} in the researched payload, "
                    "so a subscriber can rely on it being there."
                ),
            )

    return cleaned


def is_anonymous(payload_or_objects: Mapping[str, Any]) -> bool:
    """Whether the activity had no user.

    Answers the same question for a stored event's ``associatedObjects`` and for
    a built payload, so a UI can label an anonymous view without branching on
    which of the two it was handed.
    """
    objects = payload_or_objects.get("associatedObjects", payload_or_objects)
    if not isinstance(objects, Mapping):
        return True
    user = objects.get("user")
    if not isinstance(user, Mapping):
        return True
    return not any(str(value).strip() for key, value in user.items() if key != "id")


def render_associated_objects(
    objects: Mapping[str, Mapping[str, Any]], *, delivery_id: str = ""
) -> dict[str, dict[str, Any]]:
    """Expand stored objects into the researched wire shape.

    The research's own example for ``user`` is::

        "user": { "id": "6bxK1Cyh88EK", "object": "user",
                  "url": "https://api.dock.us/v1/users/6bxK1Cyh88EK",
                  "email": "john.doe@example.com" }

    so: ``id``, ``object``, ``url``, plus whatever type-specific extras the
    caller supplied (``email`` for a user, ``teamSiteId`` for an account).

    ``url`` is only rendered for the object kinds the researched pull list can
    actually resolve, and is **omitted** for the rest rather than set to null -
    see :func:`dsr.event_stream.vocabulary.associated_object_url`.
    """
    rendered: dict[str, dict[str, Any]] = {}
    for kind, entry in objects.items():
        out: dict[str, Any] = {"id": entry.get("id"), "object": kind}
        url = associated_object_url(kind, entry.get("id"))
        if url is not None:
            out["url"] = url
        for key, value in entry.items():
            if key != "id":
                out[key] = value
        rendered[kind] = out
    return rendered


# --------------------------------------------------------------------------- #
# Form questions and responses
# --------------------------------------------------------------------------- #


def normalise_form_questions(value: Any) -> list[dict[str, Any]]:
    """Check ``formQuestions``: each needs an ``id`` and a ``type``.

    Only ``file_upload`` is a name the research gives, so no closed set of
    question types is imposed here - inventing one would be inventing a
    requirement. The type is required and otherwise opaque.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise EventPayloadError(
            "formQuestions must be a non-empty list",
            code="form_questions_missing",
            remediation="workspace.form.submitted carries the questions the buyer answered.",
        )
    questions: list[dict[str, Any]] = []
    for question in value:
        if not isinstance(question, Mapping):
            raise EventPayloadError(
                f"each form question must be an object, got {type(question).__name__}",
                code="form_questions_invalid",
                remediation='Send a question as {"id": "q1", "type": "text"}.',
            )
        question_id = str(question.get("id") or "").strip()
        question_type = str(question.get("type") or "").strip()
        if not question_id:
            raise EventPayloadError(
                "each form question needs an id",
                code="form_questions_invalid",
                remediation="Responses are matched to questions by id, so the id is what pairs them.",
            )
        if not question_type:
            raise EventPayloadError(
                f"form question {question_id!r} needs a type",
                code="form_questions_invalid",
                remediation=(
                    "Questions are typed; file_upload is the one this product gives special "
                    "treatment to, because its response is a presigned URL that expires."
                ),
            )
        questions.append({**dict(question), "id": question_id, "type": question_type})
    return questions


def normalise_form_responses(
    value: Any, questions: Iterable[Mapping[str, Any]], *, now: Any = None
) -> list[dict[str, Any]]:
    """Check ``formQuestionResponses`` against ``formQuestions``.

    Two rules, both from the research:

    * a response must pair with a declared question, so a subscriber can join
      responses to questions without guessing;
    * a ``file_upload`` question's response is a presigned URL carrying
      ``expiresAt`` one hour out, and the stored response says whether that URL
      has since expired.
    """
    declared = {str(question["id"]): question for question in questions}
    if not isinstance(value, (list, tuple)):
        raise EventPayloadError(
            "formQuestionResponses must be a list",
            code="form_responses_missing",
            remediation="workspace.form.submitted carries one response per question asked.",
        )

    responses: list[dict[str, Any]] = []
    for response in value:
        if not isinstance(response, Mapping):
            raise EventPayloadError(
                f"each form response must be an object, got {type(response).__name__}",
                code="form_responses_invalid",
                remediation='Send a response as {"questionId": "q1", "value": ...}.',
            )
        question_id = str(response.get("questionId") or response.get("id") or "").strip()
        if not question_id:
            raise EventPayloadError(
                "each form response needs a questionId",
                code="form_responses_invalid",
                remediation="A response without a question cannot be shown next to the question.",
            )
        if question_id not in declared:
            raise EventPayloadError(
                f"form response {question_id!r} has no matching question",
                code="form_response_unmatched",
                remediation=(
                    f"Declared questions are {', '.join(sorted(declared)) or '(none)'}. "
                    "An unmatched response means the questions and responses came from "
                    "different submissions."
                ),
            )
        question_type = declared[question_id].get("type")
        payload_value = response.get("value")
        row: dict[str, Any] = {
            "questionId": question_id,
            "type": question_type,
            "value": payload_value,
        }
        if question_type == FILE_UPLOAD_QUESTION:
            row.update(_presigned_response(payload_value, question_id=question_id, now=now))
        responses.append(row)
    return responses


def _presigned_response(value: Any, *, question_id: str, now: Any) -> dict[str, Any]:
    """Validate and describe one ``file_upload`` response.

    A ``file_upload`` response is the researched case where a presigned URL
    rides in the payload, and the researched fact about it is that it expires
    after an hour. So the value must carry ``expiresAt``, and the row records
    whether it is still good - which is the difference between a subscriber
    reading a two-day-old event and understanding why its fetch failed.
    """
    if not isinstance(value, Mapping):
        raise EventPayloadError(
            f"file_upload question {question_id!r} needs an object response",
            code="file_upload_response_invalid",
            remediation='Send {"url": "https://...", "expiresAt": "..."}.',
        )
    url = str(value.get("url") or "").strip()
    expires_at = value.get("expiresAt") or value.get("expires_at")
    if not url:
        raise EventPayloadError(
            f"file_upload question {question_id!r} needs a url",
            code="file_upload_response_invalid",
            remediation="The presigned URL is what the buyer uploaded to.",
        )
    if not expires_at:
        raise EventPayloadError(
            f"file_upload question {question_id!r} has no expiresAt",
            code="file_upload_expiry_missing",
            remediation=(
                "Presigned URLs in a payload expire - one hour, per the research - so the "
                "response must say when. An upload with no expiry is one a subscriber cannot "
                "safely keep."
            ),
        )
    entry = {"url": url, "expiresAt": str(expires_at)}
    if "key" in value:
        entry["key"] = str(value["key"])
    return {
        "presigned": entry,
        "presigned_expired": is_expired(entry, now=now),
        "presigned_note": expiry_note(entry, now=now),
    }


# --------------------------------------------------------------------------- #
# The event record and the payload built from it
# --------------------------------------------------------------------------- #


def build_event_payload(
    data: Mapping[str, Any],
    *,
    event_id: str = "",
    now: Any = None,
    test: bool = False,
) -> dict[str, Any]:
    """Build the ``webhook-event`` body for a stored event.

    The stored record keeps the researched *inputs* in this product's own
    snake_case field names; the body is the researched *output* in the
    vendor's spelling. Keeping the two separate is what lets a subscriber be
    written against the published contract while a team can still add a field of
    their own to the record without changing what goes on the wire.
    """
    event = require_event(data.get("event"))
    occurred_at = data.get("occurred_at") or data.get("occurredAt")
    moment = parse_now(occurred_at) if occurred_at else parse_now(now)

    objects = normalise_associated_objects(
        data.get("associated_objects") or data.get("associatedObjects"),
        event=event,
    )

    payload: dict[str, Any] = {
        "object": WEBHOOK_EVENT_OBJECT,
        "event": event,
        "occurredAt": iso(moment),
        "associatedObjects": render_associated_objects(objects),
    }
    if event_id:
        payload["id"] = event_id

    property_name = data.get("property_name")
    if property_name is None:
        property_name = _default_property_name(event)
    payload["propertyName"] = str(property_name)

    # `propertyPreviousValue` is omitted rather than sent as null when there was
    # no previous value. A view has no previous state, and the research lists
    # the field as part of the payload without saying it is always present.
    previous = data.get("property_previous_value")
    if previous is not None:
        payload["propertyPreviousValue"] = previous

    if "property_value" in data and data.get("property_value") is not None:
        payload["propertyValue"] = data["property_value"]
    elif event != "workspace.created":
        payload["propertyValue"] = True

    if is_asset_event(event):
        payload["asset"] = normalise_asset_snapshot(data.get("asset"))

    if is_form_event(event):
        questions = normalise_form_questions(data.get("form_questions"))
        payload["formQuestions"] = questions
        payload["formQuestionResponses"] = normalise_form_responses(
            data.get("form_question_responses"), questions, now=now
        )

    if is_share_link_event(event):
        payload[SHARE_LINK_FIELD] = str(data.get(SHARE_LINK_FIELD) or "")

    if data.get("room_id"):
        payload["roomId"] = str(data["room_id"])
    if data.get("account"):
        payload["account"] = str(data["account"])
    if test or data.get("test"):
        payload["test"] = True

    metadata = data.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        payload["metadata"] = dict(metadata)

    return payload


#: What ``propertyName`` is when the caller does not name one.
#:
#: Not a researched default - the research says the field is on the payload but
#: does not say what it holds for a view. Inference
#: ``property-name-defaults-to-the-event``; a caller that cares passes one.
_PROPERTY_NAME_PREFIXES: tuple[tuple[str, str], ...] = (
    ("workspace.", "workspace"),
    ("asset.", "asset"),
    ("presentation.", "presentation"),
    ("course.", "workspacePlanTask"),
)


def _default_property_name(event: str) -> str:
    for prefix, object_kind in _PROPERTY_NAME_PREFIXES:
        if event.startswith(prefix):
            return f"{object_kind}.activity"
    return "workspace.activity"


def normalise_asset_snapshot(value: Any) -> dict[str, Any]:
    """Validate the ``asset`` snapshot an ``asset.*`` event carries.

    The researched snapshot fields are ``name``, ``type``, ``shareUrl``,
    ``isInternal``, ``tags``, ``downloadEnabled`` and ``trackingEnabled``.
    ``trackingEnabled`` is the one this build insists on: it is what
    "distinguishes gated share links", so a payload without it cannot be
    attributed to a tracked link and a subscriber counting engagement would be
    counting the wrong thing.
    """
    if not isinstance(value, Mapping) or not value:
        raise EventPayloadError(
            "an asset.* event must carry an asset snapshot",
            code="asset_snapshot_missing",
            remediation=(
                'Send the asset snapshot, at minimum {"name": ..., "type": ..., '
                '"trackingEnabled": false}.'
            ),
        )
    snapshot = dict(value)
    for required in ("name", "type", "trackingEnabled"):
        if required not in snapshot:
            raise EventPayloadError(
                f"the asset snapshot is missing {required!r}",
                code="asset_snapshot_incomplete",
                remediation=(
                    "The researched asset snapshot carries name, type, shareUrl, isInternal, "
                    "tags, downloadEnabled and trackingEnabled. trackingEnabled is what tells a "
                    "gated share link from an ordinary view."
                ),
            )
    snapshot["tags"] = [str(tag) for tag in (snapshot.get("tags") or [])]
    snapshot["trackingEnabled"] = bool(snapshot.get("trackingEnabled"))
    return snapshot
