"""The researched vocabulary of WF-025, in one place.

Everything in this module is a *name* the research fixed, or an explicit
statement that it did not. The point of collecting them is that a client
renders its pickers from here rather than from a hard-coded list in a feature
file, so an event type added in one place reaches every client at once.

The sourced list
----------------
``docs/research/digital-sales-room-workflows/wf/WF-025.md`` records, under
``apis_hit``:

    "subscription types include ``workspace.created``, ``workspace.viewed``,
    ``workspace.page.viewed``, ``workspace.section_navigation.clicked``,
    ``workspace.file.viewed``, ``workspace.file.downloaded``,
    ``workspace.link.clicked``, ``workspace.text_link.clicked``,
    ``workspace.embed.interacted``, ``workspace.custom_code.interacted``,
    ``workspace.order_form.viewed|downloaded|signed|fully_signed``,
    ``workspace.NDA.signed``, ``workspace.form.submitted``,
    ``asset.viewed|shared|downloaded``, ``presentation.viewed|downloaded|shared``,
    ``course.completed|course.reviewed``."

Read that word **include** carefully, because it decides a behaviour below: the
research presents the list as a set that *includes* these names, not as an
exhaustive enumeration, so this package treats the list as a **floor** and
accepts an event type outside it. See :func:`require_event` and the
``event-type-set-is-a-floor`` entry in :mod:`dsr.event_stream.inferences`.

The odd spellings are reproduced verbatim, spaces and case included, because
these strings go over the wire to somebody else's subscription and a
"tidied" ``workspace.NDA.signed`` would silently never match.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.event_stream.errors import VocabularyError

# --------------------------------------------------------------------------- #
# Event types
# --------------------------------------------------------------------------- #

#: Every subscription type the research names, in the order it names them.
#: Reproduced verbatim from the quote above, ``|`` expanded.
EVENT_TYPES: tuple[str, ...] = (
    "workspace.created",
    "workspace.viewed",
    "workspace.page.viewed",
    "workspace.section_navigation.clicked",
    "workspace.file.viewed",
    "workspace.file.downloaded",
    "workspace.link.clicked",
    "workspace.text_link.clicked",
    "workspace.embed.interacted",
    "workspace.custom_code.interacted",
    "workspace.order_form.viewed",
    "workspace.order_form.downloaded",
    "workspace.order_form.signed",
    "workspace.order_form.fully_signed",
    "workspace.NDA.signed",
    "workspace.form.submitted",
    "asset.viewed",
    "asset.shared",
    "asset.downloaded",
    "presentation.viewed",
    "presentation.downloaded",
    "presentation.shared",
    "course.completed",
    "course.reviewed",
)

#: The set as a lookup, for membership tests.
KNOWN_EVENTS: frozenset[str] = frozenset(EVENT_TYPES)


# --------------------------------------------------------------------------- #
# associatedObjects
# --------------------------------------------------------------------------- #


#: The object kinds a ``webhook-event`` can carry in ``associatedObjects``,
#: from the researched data flow: "``associatedObjects`` (``workspace``,
#: ``account``, ``user``, plus ``workspacePage`` / ``workspaceSection`` /
#: ``workspacePlanTask`` / ``file`` / ``workspaceForm``)".
#:
#: Note the casing. Four of these are camelCase while the event *types* are
#: dotted lower case, and both spellings are the vendor's. They are reproduced
#: as written rather than normalised, for the same reason the event types are.
ASSOCIATED_OBJECTS: tuple[str, ...] = (
    "workspace",
    "account",
    "user",
    "workspacePage",
    "workspaceSection",
    "workspacePlanTask",
    "file",
    "workspaceForm",
)

#: Always present on an event. The research names these two first and every
#: event in its examples carries them.
ALWAYS_ASSOCIATED: tuple[str, ...] = ("workspace", "account")

#: Present only when the activity had a user. "Anonymous activity omits
#: ``user``" - so the key is *absent*, not null, and that difference is
#: observable by a subscriber.
OPTIONAL_ASSOCIATED: tuple[str, ...] = ("user",)


#: Which extra object kinds each event type must carry, beyond
#: :data:`ALWAYS_ASSOCIATED`.
#:
#: Three of these are sourced and the rest are inferences, marked inline. The
#: rule that matters operationally is that the *event types* the research pairs
#: with a named object kind must carry it, so a subscriber can rely on
#: ``associatedObjects.workspacePage`` existing for ``workspace.page.viewed``.
EVENT_OBJECTS: dict[str, tuple[str, ...]] = {
    # [sourced] The research names workspacePage / workspaceSection / file /
    # workspaceForm / workspacePlanTask as associated objects and pairs the
    # event types with them by name.
    "workspace.page.viewed": ("workspacePage",),
    "workspace.section_navigation.clicked": ("workspaceSection",),
    "workspace.file.viewed": ("file",),
    "workspace.file.downloaded": ("file",),
    "workspace.form.submitted": ("workspaceForm",),
    "course.completed": ("workspacePlanTask",),
    "course.reviewed": ("workspacePlanTask",),
    # [inferred] The order form and the NDA are documents the buyer opens,
    # downloads, and signs, and `file` is the object kind the research gives
    # for exactly that. Recorded as inference `order-form-and-nda-carry-file`.
    "workspace.order_form.viewed": ("file",),
    "workspace.order_form.downloaded": ("file",),
    "workspace.order_form.signed": ("file",),
    "workspace.order_form.fully_signed": ("file",),
    "workspace.NDA.signed": ("file",),
}


# --------------------------------------------------------------------------- #
# Event families with their own sourced rules
# --------------------------------------------------------------------------- #

#: "``presentation.viewed`` and ``presentation.downloaded`` are emitted for
#: presentation share-link activity only. Asset link activity is available
#: through ``asset.viewed``, ``asset.shared``, and ``asset.downloaded``."
#:
#: The quote names ``viewed`` and ``downloaded``; the type list also publishes
#: ``presentation.shared``. All three are treated as share-link scoped, because
#: the sentence is about the share link as the *only* source of presentation
#: activity, not about two of the three verbs. Recorded as inference
#: `presentation-shared-is-also-share-link-scoped`.
SHARE_LINK_EVENTS: tuple[str, ...] = (
    "presentation.viewed",
    "presentation.downloaded",
    "presentation.shared",
)

#: "Asset link activity is available through ``asset.viewed``, ``asset.shared``,
#: and ``asset.downloaded``." These carry the asset snapshot at the top level of
#: the payload rather than in ``associatedObjects`` - see
#: :mod:`dsr.event_stream.payloads`.
ASSET_EVENTS: tuple[str, ...] = ("asset.viewed", "asset.shared", "asset.downloaded")

#: "``workspace.form.submitted`` payloads carry ``formQuestions`` +
#: ``formQuestionResponses`` (typed questions incl. ``file_upload``)".
FORM_EVENTS: tuple[str, ...] = ("workspace.form.submitted",)

#: The one question type the research names. Every other question type is
#: accepted as an opaque string, because inventing a closed list of them would
#: be inventing a requirement the research does not make.
FILE_UPLOAD_QUESTION = "file_upload"

#: The asset snapshot fields the research documents for ``asset.*`` payloads:
#: "the ``asset.viewed`` / ``asset.downloaded`` webhooks whose payload embeds
#: the ``asset`` snapshot (``name``, ``type``, ``shareUrl``, ``isInternal``,
#: ``tags``, ``downloadEnabled``, ``trackingEnabled``)" (workflow 3 of the same
#: research corpus, cross-referenced by workflow 10).
#:
#: ``trackingEnabled`` is the one that matters: it "distinguishes gated share
#: links" from ordinary views, so a subscriber counting real engagement has to
#: be able to tell the two apart.
ASSET_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "name",
    "type",
    "shareUrl",
    "isInternal",
    "tags",
    "downloadEnabled",
    "trackingEnabled",
)


#: Which associated object kinds the researched pull list can resolve to a URL
#: this product actually serves.
#:
#: ``workspace`` is item-addressable because the research publishes
#: ``GET /v1/workspaces/{id}``. ``workspacePlanTask`` resolves only through its
#: collection path, because the research publishes no item path for it.
#: Everything else - ``account``, ``file``, ``user``, ``workspaceForm`` - has no
#: pull route in *this* workflow, so its payload entry carries **no** ``url``
#: rather than a fabricated one. See ``inferences.py`` entry
#: ``associated-object-url``.
ASSOCIATED_OBJECT_URLS: dict[str, str] = {
    "workspace": "workspaces/{workspaceId}",
    "workspacePlanTask": "workspace-plan-tasks",
}

# --------------------------------------------------------------------------- #
# Seismic's parallel contract for the same role
# --------------------------------------------------------------------------- #

#: "``DSRCreatedV1``, ``DSRUpdatedV1``, ``DSRExpiredV1``,
#: ``DSRContentUpdatedV1``" - the same job done by a second vendor, cited by this
#: workflow so the behaviour can be checked against a peer rather than invented.
#:
#: Published next to ours rather than mapped onto ours. A subscriber that also
#: consumes Seismic needs to know the names differ; a mapping table claiming
#: ``workspace.file.viewed == DSRContentUpdatedV1`` would be a guess.
SEISMIC_EVENTS: tuple[str, ...] = (
    "DSRCreatedV1",
    "DSRUpdatedV1",
    "DSRExpiredV1",
    "DSRContentUpdatedV1",
)


# --------------------------------------------------------------------------- #
# The pull-based backfill surface
# --------------------------------------------------------------------------- #

#: "REST API for pull-based backfill: base ``https://api.dock.us``,
#: ``Authorization: Bearer <Your-Token>``; ``GET /v1/workspaces``,
#: ``GET /v1/workspaces/{id}``, ``GET /v1/assets``,
#: ``GET /v1/forms/{id}/responses``, ``GET /v1/workspace-plan-tasks``;
#: ``properties`` query param; ``429`` on rate limit."
#:
#: Each entry maps a researched path onto the collection this product already
#: holds that resource in. The mapping is the whole content of the inference:
#: there is no source schema here, only a source route list.
#:
#: ``vendor_path`` is the researched spelling, kept so the vocabulary can show a
#: client what the source calls it. ``route`` is the path this product actually
#: serves, and the two are deliberately different: emitting the researched path
#: in a payload would be a link this app does not answer, which is the same
#: defect as a fabricated ``url`` on an associated object.
PULL_RESOURCES: dict[str, dict[str, Any]] = {
    "workspaces": {
        "collection": "room",
        "object": "workspace",
        "vendor_path": "/v1/workspaces",
        "route": "workspaces",
        "id_field": "id",
    },
    "workspaces/{workspaceId}": {
        "collection": "room",
        "object": "workspace",
        "vendor_path": "/v1/workspaces/{workspaceId}",
        "route": "workspaces/{workspaceId}",
        "id_field": "id",
    },
    "assets": {
        "collection": "document",
        "object": "asset",
        "vendor_path": "/v1/assets",
        "route": "assets",
        "id_field": "id",
    },
    "forms/{formId}/responses": {
        "collection": "form_response",
        "object": "formResponse",
        "vendor_path": "/v1/forms/{formId}/responses",
        "route": "forms/{formId}/responses",
        "id_field": "formId",
    },
    "workspace-plan-tasks": {
        "collection": "plan_task",
        "object": "workspacePlanTask",
        "vendor_path": "/v1/workspace-plan-tasks",
        "route": "workspace-plan-tasks",
        "id_field": "id",
    },
}

#: "Endpoints that return a resource accept a ``properties`` query parameter
#: that controls which fields are included in the response. If you omit it, the
#: response contains **only** the resource's ``id``, ``object``, and ``url``."
#:
#: The omission behaviour is the part that bites, so it is a constant rather
#: than an `if`: the minimal projection is what a caller gets for free, and
#: asking for nothing must never accidentally mean asking for everything.
MINIMAL_PROPERTIES: tuple[str, ...] = ("id", "object", "url")


# --------------------------------------------------------------------------- #
# Delivery policy
# --------------------------------------------------------------------------- #

#: "Seismic will wait for **10 seconds** for the webhook to respond."
WEBHOOK_TIMEOUT_SECONDS = 10.0

#: "Total number of retries: 26", and the ladder is "1 min, then 10 min, then
#: hourly x24". One minute, one ten-minute, twenty-four hourly: 26 steps, which
#: is exactly the documented total. Encoded as a tuple of *delays before each
#: retry* rather than as a count, because the shape of the ladder is the policy
#: and a count would throw away everything a scheduler needs to plan around.
RETRY_LADDER: tuple[float, ...] = (60.0, 600.0) + (3600.0,) * 24

#: The documented total, asserted against the ladder rather than restated, so
#: the two cannot drift.
MAX_RETRIES: int = len(RETRY_LADDER)


# --------------------------------------------------------------------------- #
# Vocabulary helpers
# --------------------------------------------------------------------------- #


def is_known_event(event: Any) -> bool:
    """Whether ``event`` is one of the published subscription types.

    False is not a refusal here. The research says the list *includes* these
    names, so a type outside the set is a type this build has not read about,
    not one that does not exist. See :func:`require_event`.
    """
    return event in KNOWN_EVENTS


def require_event(event: Any) -> str:
    """Normalise one subscription type.

    Refuses only what cannot be a type at all: a missing value, a non-string,
    or whitespace. An unrecognised-but-well-formed name is **kept**, because the
    research's own sentence is "subscription types *include* ..."; a vendor that
    publishes a new type must not have its subscribers broken by a stale list on
    someone else's deployment.

    Callers that want to warn about the gap ask :func:`is_known_event`
    afterwards. The routes and the UI both do.
    """
    if event is None or (isinstance(event, str) and not event.strip()):
        raise VocabularyError("an event type is required", remediation="Send a type string.")
    if not isinstance(event, str):
        raise VocabularyError(
            f"event type must be a string, got {type(event).__name__}",
            remediation="Send the dotted type name, for example workspace.viewed.",
        )
    return event.strip()


def require_types(types: Any) -> tuple[str, ...]:
    """Normalise the ``types`` a subscription asks for.

    Order is preserved and duplicates collapse, so ``["a", "b", "a"]`` is the
    subscription ``["a", "b"]`` rather than one that reports three types and
    delivers two.
    """
    if isinstance(types, str):
        # A single type sent as a bare string is a client that would otherwise
        # get the "no types" refusal for no good reason.
        types = [types]
    if not isinstance(types, (list, tuple)):
        raise VocabularyError(
            f"types must be a list, got {type(types).__name__}",
            remediation="Send a list of subscription type names.",
        )
    seen: list[str] = []
    for candidate in types:
        normalised = require_event(candidate)
        if normalised not in seen:
            seen.append(normalised)
    if not seen:
        raise VocabularyError(
            "a subscription must ask for at least one type",
            remediation=(
                "Pick at least one of the published subscription types; "
                "GET /api/wf-025/vocabulary lists them."
            ),
        )
    return tuple(seen)


def unknown_types(types: Iterable[str]) -> list[str]:
    """Which of ``types`` are not in the published set.

    Reported, never refused - see :func:`require_event`.
    """
    return [event for event in types if not is_known_event(event)]


def required_objects_for(event: str) -> tuple[str, ...]:
    """Every ``associatedObjects`` key ``event`` must carry."""
    return ALWAYS_ASSOCIATED + EVENT_OBJECTS.get(event, ())


def is_share_link_event(event: str) -> bool:
    """Whether ``event`` is only emitted for share-link activity."""
    return event in SHARE_LINK_EVENTS


def is_asset_event(event: str) -> bool:
    """Whether ``event`` carries the asset snapshot at the top level."""
    return event in ASSET_EVENTS


def is_form_event(event: str) -> bool:
    """Whether ``event`` carries ``formQuestions`` + ``formQuestionResponses``."""
    return event in FORM_EVENTS


def describe_event(event: str) -> dict[str, Any]:
    """Everything this package knows about one subscription type.

    Served at ``/vocabulary`` so a picker can render the object kinds a
    subscriber may rely on for a given type, rather than making the subscriber
    discover it by trial and error.
    """
    required = required_objects_for(event)
    return {
        "type": event,
        "known": is_known_event(event),
        "required_associated_objects": list(required),
        "optional_associated_objects": list(OPTIONAL_ASSOCIATED),
        "share_link_scoped": is_share_link_event(event),
        "carries_asset_snapshot": is_asset_event(event),
        "carries_form_responses": is_form_event(event),
    }


def vocabulary() -> dict[str, Any]:
    """The whole published vocabulary, as one payload."""
    return {
        "event_types": list(EVENT_TYPES),
        "event_count": len(EVENT_TYPES),
        "events": [describe_event(event) for event in EVENT_TYPES],
        "associated_objects": list(ASSOCIATED_OBJECTS),
        "always_associated": list(ALWAYS_ASSOCIATED),
        "optional_associated": list(OPTIONAL_ASSOCIATED),
        "share_link_events": list(SHARE_LINK_EVENTS),
        "asset_events": list(ASSET_EVENTS),
        "form_events": list(FORM_EVENTS),
        "file_upload_question": FILE_UPLOAD_QUESTION,
        "asset_snapshot_fields": list(ASSET_SNAPSHOT_FIELDS),
        "seismic_events": list(SEISMIC_EVENTS),
        "pull_resources": {name: dict(spec) for name, spec in PULL_RESOURCES.items()},
        "minimal_properties": list(MINIMAL_PROPERTIES),
        "delivery": {
            "timeout_seconds": WEBHOOK_TIMEOUT_SECONDS,
            "retry_ladder_seconds": list(RETRY_LADDER),
            "max_retries": MAX_RETRIES,
            "max_attempts": MAX_RETRIES + 1,
        },
    }


def backfill_route(route: str, **substitutions: Any) -> str:
    """The URL this product serves for one researched pull path.

    ``vendor_path`` is what the source calls the resource; ``route`` is what
    this app answers. Only the second may appear in a payload, because a url a
    subscriber can follow is a promise that GETting it works.
    """
    rendered = route
    for key, value in substitutions.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return "/api/wf-025/backfill/" + rendered


def associated_object_url(kind: str, object_id: Any) -> str | None:
    """The pull URL for an associated object, or ``None`` when there is none.

    ``None`` means the ``url`` key is left out of the payload entirely, which is
    different from ``"url": null``: a subscriber can then tell "this product
    does not expose a URL for this object kind" from "this object's URL is
    missing", and neither is a broken link.
    """
    route = ASSOCIATED_OBJECT_URLS.get(kind)
    if route is None:
        return None
    return backfill_route(route, workspaceId=object_id)


def project_properties(
    record: Mapping[str, Any], spec: Mapping[str, Any], properties: list[str]
) -> dict[str, Any]:
    """Project one record down to ``properties`` for a pull response.

    ``MINIMAL_PROPERTIES`` is honoured by the caller, not here: it is a rule
    about what the *absence* of the parameter means, and this function only
    answers "these fields, or these".
    """
    out: dict[str, Any] = {}
    for name in properties:
        if name == "id":
            out["id"] = record.get("id")
        elif name == "object":
            out["object"] = spec.get("object")
        elif name == "url":
            out["url"] = backfill_route(
                str(spec["route"]),
                workspaceId=record.get("id"),
                formId=record.get("id"),
            )
        else:
            out[name] = (record.get("data") or {}).get(name)
    return out
