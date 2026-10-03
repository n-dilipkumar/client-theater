"""The domain behind WF-044: a CRM-side webhook action, aimed at one room.

The research document is the specification -
``docs/research/digital-sales-room-workflows/wf/WF-044.md`` (section 11 of
``docs/research/raw/crm-integration.md``).

The researched flow, as the spec states it
------------------------------------------
1. The rep sets the automation up **in the CRM**: *Automation -> Workflows -> edit
   workflow -> + -> Data ops -> Send a webhook*.
2. They add start conditions: *"deal stage becomes 'Contract Sent'"*, or *"a
   contact property changes"*.
3. They pick method **POST**, enter the room's HTTPS webhook URL, and configure
   authentication.
4. They choose the body: **Include all [object] properties**, or **Customize
   request body** (properties as key/value, and/or static values).
5. They click **Save**, then **Publish**, then use the built-in **Test** control.
6. *"The room's endpoint verifies the signature, resolves the record, and updates
   the buyer's room state."*

What this package implements
---------------------------
* **One endpoint per room, many automations against it.** The research says it
  outright: *"The room exposes one inbound endpoint per tenant with a versioned
   payload contract, so any number of CRM-side automations can target it. Because
   the room can verify the request signature, it does not need a per-workflow
   secret."* A second endpoint per room is refused for that reason, not because
  registering two is awkward.
* **The URL must be HTTPS** - *"Webhook URLs are restricted to a secure protocol
  and must begin with HTTPS."*
* **POST and GET, both served** - *"You can send both POST and GET requests using
  workflows."* A GET's properties arrive in the query string, so the receive path
  reads the same contract out of both, and signs an empty body because there is
  none.
* **Three authentication types and no others** - request signature plus a HubSpot
  App ID; an API key in a header or in query params; ``Bearer [YOUR_TOKEN]``.
* **Two body modes** - *Include all [object] properties* and *Customize request
  body* - read by one rule in :mod:`dsr.crm_outbound_webhooks.payloads`.
* **Publish before it goes live** - *"Workflows must be **published** to go
  live."* A delivery to a saved-but-unpublished endpoint is recorded and turned
  away, because a rep who forgot to click Publish needs to find that out.
* **Two permissions, not one** - *"To set up webhook actions in workflows, users
  must have Edit permissions for workflows or Super Admin permissions. To publish
  workflows, users must have Publish permissions for workflows."*
* **1,000 subscriptions per app** - *"You can create up to 1,000 webhook
  subscriptions per app."* Counted across rooms, because the source counts per app.
* **Workflow webhook calls are not rate limited against the API budget** - *"Webhook
  calls made via workflows do not count towards the API rate limit."* The room
  applies no inbound quota and says so where a reader would look for one. Its
  obligation runs the other way: *"When a webhook is slow or times out, the
  workflow action may take longer than expected to execute"*, so a delivery is one
  transaction and opens no socket.
* **Only this product's own writes.** Nothing here calls the CRM. The researched
  flow is inbound to the room, and this build implements its whole second half:
  *"signed POST to the room endpoint -> room authenticates (signature / bearer) ->
  maps payload onto the room model -> updates deal panel and notifies the rep."*

Schema flexibility
------------------
Every field this package stores is ordinary JSON in ``records.data``. The two
room-side field names - the property carrying the external id, and the property
carrying the stage - are named on the endpoint record, so a team whose CRM calls
them something else configures them once and no code changes. Every other property
in a payload is kept verbatim: a field nobody coordinated with us still gets
stored. There is no migration and no typed column.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from dsr.crm_outbound_webhooks.errors import (
    AlreadyPublished,
    AutomationLimitReached,
    EmptyPayload,
    EndpointExists,
    EndpointNotPublished,
    EndpointStateConflict,
    InvalidAuthSetting,
    InvalidEndpointUrl,
    InvalidRequest,
    InvalidTrigger,
    MalformedBody,
    MissingAppId,
    MissingPermission,
    MissingSecret,
    NoEndpoint,
    ObjectMismatch,
    UnauthenticatedDelivery,
    UnknownAuthMode,
    UnknownAutomation,
    UnknownBodyMode,
    UnresolvedDeal,
    UnsupportedMethod,
    WebhookError,
)
from dsr.crm_outbound_webhooks.inferences import describe as describe_inferences
from dsr.crm_outbound_webhooks.payloads import read_payload, sample_body
from dsr.crm_outbound_webhooks.signing import canonical_string, sign, verify
from dsr.crm_outbound_webhooks.vocabulary import (
    API_KEY_LOCATIONS,
    AUTH_MODES,
    BEARER_PREFIX,
    BODY_INHERIT,
    BODY_MODES,
    COLLECTION_AUTOMATION,
    COLLECTION_DEAL,
    COLLECTION_DELIVERY,
    COLLECTION_ENDPOINT,
    COLLECTION_NOTICE,
    CONTRACT_VERSION,
    EFFECT_NONE,
    EFFECT_PROPERTIES_ONLY,
    EFFECT_STAGE_CHANGED,
    EFFECT_STAGE_UNCHANGED,
    METHODS,
    OBJECTS,
    OUTCOME_ACCEPTED,
    OUTCOME_DUPLICATE,
    OUTCOME_REFUSED,
    PUBLISH_PERMISSIONS,
    PUBLISH_RULE,
    REASONS,
    REPLAY_WINDOW_SECONDS,
    SETUP_PERMISSIONS,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    SUBSCRIPTION_LIMIT_PER_APP,
    default_id_key,
    default_stage_key,
    describe as describe_vocabulary,
)
from dsr.store import RecordStore

__all__ = ["WebhookEngine", "uri_for"]

#: The path a rep pastes into **Enter the webhook URL**.
#:
#: Kept in the domain rather than in the feature module because the sample
#: preview, the settings page and the route that serves it must all name the same
#: path, and a version number in three files is three chances to disagree. The
#: room id is filled in by the caller, which is the only part of it this package
#: cannot know.
URI_TEMPLATE = f"/api/wf-044/rooms/{{room_id}}/crm/v{CONTRACT_VERSION}/webhook"


def uri_for(room_id: str) -> str:
    """The full endpoint path for one room."""
    return URI_TEMPLATE.format(room_id=room_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _as_permissions(value: Any) -> set[str]:
    """Accept ``"a,b"``, ``["a", "b"]`` or a set, and lowercase it.

    A query string is a string, a JSON body is a list, and a test wants a set. One
    normaliser means the served vocabulary can be handed straight back by a client
    without it having to know which of the three the route takes.
    """
    if value is None:
        return set()
    if isinstance(value, str):
        return {part.strip().lower() for part in value.split(",") if part.strip()}
    if isinstance(value, Mapping):
        return {str(key).strip().lower() for key, held in value.items() if held}
    if isinstance(value, Iterable):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return {str(value).strip().lower()}


class WebhookEngine:
    """Every rule of the researched workflow, over one :class:`RecordStore`.

    ``source`` is a *required* keyword on every writing method. A URL string
    hardcoded inside one of these is the defect the audit-source rule exists to
    prevent - an audit row naming a route the app has stopped serving - and
    requiring it turns the mistake into a ``TypeError`` at the call site rather
    than an untraceable row in production.
    """

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    # -- vocabulary ---------------------------------------------------------- #

    def vocabulary(self) -> dict[str, Any]:
        """Every published vocabulary, served as data."""
        return describe_vocabulary()

    def inferences(self) -> dict[str, Any]:
        """Every judgement call this package rests on, and how to change each one."""
        return describe_inferences()

    # -- permissions --------------------------------------------------------- #

    @staticmethod
    def _require(granted: Any, allowed: Sequence[str], action: str) -> None:
        """The researched permission rule, failing closed.

        No permission presented means no permission held. A room whose operator
        forgets the query parameter gets a 403 that names the exact strings, not a
        silent success - the same reasoning as ``dsr.permissions`` failing closed
        on an unrecognised role.
        """
        held = _as_permissions(granted)
        if held & set(allowed):
            return
        raise MissingPermission(
            f"{action} requires {' or '.join(repr(name) for name in allowed)}; "
            f"the caller presented {sorted(held) or ['nothing']}"
        )

    # -- endpoint configuration ---------------------------------------------- #

    @staticmethod
    def _normalise_url(value: Any) -> str:
        """The researched URL rule, enforced at save time.

        *"Webhook URLs are restricted to a secure protocol and must begin with
        HTTPS."* The CRM refuses such a URL at its own **Save**, so accepting one
        here would leave this product holding a row the researched tool will never
        let the rep complete.
        """
        url = str(value or "").strip()
        if not url:
            raise InvalidEndpointUrl(
                "an endpoint needs the HTTPS URL the rep types into the CRM's "
                "**Enter the webhook URL** field"
            )
        if not url.lower().startswith("https://"):
            raise InvalidEndpointUrl(
                "Webhook URLs are restricted to a secure protocol and must begin "
                f"with HTTPS; got {url!r}"
            )
        if not url.split("://", 1)[1].split("/", 1)[0]:
            raise InvalidEndpointUrl(f"the URL has no host: {url!r}")
        return url

    @staticmethod
    def _normalise_method(value: Any) -> str:
        method = str(value or "POST").strip().upper()
        if method not in METHODS:
            raise UnsupportedMethod(
                f"You can send both POST and GET requests using workflows; {method!r} is neither"
            )
        return method

    @staticmethod
    def _normalise_auth(value: Any) -> dict[str, Any]:
        """The three researched authentication types, and their required fields.

        * ``signature`` - *"Then, enter your HubSpot App ID."* The App ID is what
          the room binds the signature to, so without one there is nothing to check
          against.
        * ``api_key`` - *"Set the value of API key name to Authorization. Set the
          value of API key location to Request Header"*, or a query parameter.
        * ``bearer`` - *"The secret value must be in the format
          `Bearer [YOUR_TOKEN]`"*. The stored secret is therefore the token; a
          value pasted with the ``Bearer `` prefix still works, because the vendor
          documentation is what a rep copies from.
        """
        raw = dict(value or {}) if isinstance(value, Mapping) else {}
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in AUTH_MODES:
            raise UnknownAuthMode(
                "the researched authentication types are "
                f"{list(AUTH_MODES)}; got {raw.get('mode')!r}"
            )
        secret = str(raw.get("secret") or "").strip()
        auth: dict[str, Any] = {"mode": mode, "secret": secret}

        if mode == "signature":
            app_id = str(raw.get("app_id") or "").strip()
            if not app_id:
                raise MissingAppId(
                    "Include request signature in header requires a HubSpot App ID: "
                    "select **Include request signature in header**, then enter your "
                    "HubSpot App ID"
                )
            auth["app_id"] = app_id
            if not secret:
                raise MissingSecret(
                    "signature authentication needs the client secret the signature "
                    "is computed with"
                )
            auth["replay_window_seconds"] = int(
                raw["replay_window_seconds"]
                if raw.get("replay_window_seconds") is not None
                else REPLAY_WINDOW_SECONDS
            )
            return auth

        if mode == "api_key":
            if not secret:
                raise MissingSecret(
                    "an api_key endpoint needs the key the rep pastes into the CRM's API key field"
                )
            name = str(raw.get("name") or "api_key").strip() or "api_key"
            location = str(raw.get("location") or "header").strip().lower()
            if location not in API_KEY_LOCATIONS:
                raise InvalidAuthSetting(
                    "API key location must be one of "
                    f"{list(API_KEY_LOCATIONS)}; got {raw.get('location')!r}"
                )
            auth["name"] = name
            auth["location"] = location
            return auth

        if not secret:
            raise MissingSecret("The secret value must be in the format `Bearer [YOUR_TOKEN]`")
        form = "token"
        if secret.lower().startswith(BEARER_PREFIX.strip().lower() + " "):
            secret = secret[len(BEARER_PREFIX) :].strip()
            form = "bearer_prefixed"
        # A secret that is only the scheme word is a paste of the documentation, not
        # a token, and storing it would accept a request carrying the word "Bearer"
        # and nothing else.
        if not secret or secret.lower() == BEARER_PREFIX.strip().lower():
            raise MissingSecret("The secret value must be in the format `Bearer [YOUR_TOKEN]`")
        auth["secret"] = secret
        auth["secret_form"] = form
        return auth

    @staticmethod
    def _normalise_body(value: Any) -> dict[str, Any]:
        """The two researched body modes, and the key table the second one is.

        *"enter the Key and select a property"*, *"To add a static field, enter the
        Key and Value"*, *"To add another property, click **Add static value**"*. So
        a row is a key, a source, and - for a static value - the value. A row with
        neither source has nothing to send.
        """
        raw = dict(value or {}) if isinstance(value, Mapping) else {}
        mode = str(raw.get("mode") or "include_all").strip().lower()
        if mode not in BODY_MODES:
            raise UnknownBodyMode(
                "the researched body modes are **Include all [object] properties** "
                f"and **Customize request body**; got {raw.get('mode')!r}"
            )
        if mode == "include_all":
            if raw.get("keys"):
                raise MalformedBody(
                    "**Include all [object] properties** sends every property, so a "
                    "key list cannot also be set; use **Customize request body**"
                )
            return {"mode": mode, "keys": []}

        entries = raw.get("keys")
        if entries in (None, ""):
            entries = []
        if not isinstance(entries, (list, tuple)):
            raise MalformedBody(
                f"**Customize request body** is a table of keys; got {type(entries).__name__}"
            )
        keys: list[dict[str, Any]] = []
        for position, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise MalformedBody(f"key {position} is not an object")
            key = str(entry.get("key") or "").strip()
            if not key:
                raise MalformedBody(
                    f"key {position} has no Key; the CRM's body editor needs one "
                    "before a property or a static value can be added"
                )
            prop = entry.get("property")
            static = entry.get("value")
            prop = str(prop).strip() if prop not in (None, "") else None
            if prop is None and static in (None, ""):
                raise MalformedBody(
                    f"key {key!r} has neither a property nor a static value; "
                    "**Customize request body** asks for one or the other"
                )
            keys.append(
                {
                    "key": key,
                    "kind": "property" if prop is not None else "static",
                    "property": prop,
                    "value": static if prop is None else None,
                }
            )
        return {"mode": mode, "keys": keys}

    def _normalise_endpoint(
        self, payload: Mapping[str, Any], existing: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate the whole endpoint, merged over the one already stored.

        Re-validated whole rather than field by field, for the reason
        :meth:`amend` gives: an amendment must not be able to smuggle past a rule
        another key would have broken.
        """
        current = dict(existing or {})
        merged = {**current, **dict(payload or {})}
        obj = str(merged.get("object") or "deals").strip().lower()
        if obj not in OBJECTS:
            raise ObjectMismatch(
                f"the researched objects are {list(OBJECTS)}; got {merged.get('object')!r}"
            )
        auth_source = payload.get("auth") if "auth" in payload else current.get("auth")
        body_source = payload.get("body") if "body" in payload else current.get("body")
        return {
            "object": obj,
            "method": self._normalise_method(merged.get("method") or current.get("method")),
            "url": self._normalise_url(merged.get("url") or current.get("url")),
            "auth": self._normalise_auth(auth_source),
            "body": self._normalise_body(body_source),
            "id_key": str(merged.get("id_key") or current.get("id_key") or default_id_key(obj)),
            "stage_key": str(
                merged.get("stage_key") or current.get("stage_key") or default_stage_key(obj)
            ),
            "name": str(merged.get("name") or current.get("name") or "CRM webhook"),
            "notes": merged.get("notes") or current.get("notes") or "",
        }

    def endpoint(self, room_id: str) -> dict[str, Any] | None:
        """The room's one endpoint, or ``None``."""
        rows = self.store.list(COLLECTION_ENDPOINT, room_id=room_id, limit=10)
        return rows[0] if rows else None

    def require_endpoint(self, room_id: str) -> dict[str, Any]:
        found = self.endpoint(room_id)
        if found is None:
            raise NoEndpoint(
                f"room {room_id} has no inbound webhook endpoint, so no URL has ever "
                "been given to the CRM for it"
            )
        return found

    def register(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Steps 1 to 4, as the room records them. Nothing is delivered yet.

        Draft until :meth:`publish`, because *"Workflows must be **published** to go
        live"* and the room honours the same rule for the automation aimed at it.
        """
        self._require(permissions, SETUP_PERMISSIONS, "setting up a webhook action")
        if self.endpoint(room_id) is not None:
            raise EndpointExists(
                "this room already has an endpoint. The room exposes one inbound "
                "endpoint per tenant so any number of CRM-side automations can "
                "target it; add another automation instead."
            )
        data = self._normalise_endpoint(payload)
        data["status"] = STATUS_DRAFT
        data["contract_version"] = CONTRACT_VERSION
        data["published_at"] = None
        data["automation_count"] = 0
        return self.store.create(
            COLLECTION_ENDPOINT, data, room_id=room_id, actor=actor, source=source
        )

    def amend(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Correct the configuration - the rep's edit-and-republish, room-side.

        A published endpoint is amendable, which is the judgement call
        ``published-endpoints-are-amendable`` records. Unpublishing first is the
        stricter reading; refusing here would strand a rep who mistyped a URL on a
        live integration, and every change is already an audit row naming this
        route.
        """
        self._require(permissions, SETUP_PERMISSIONS, "changing a webhook action")
        current = self.require_endpoint(room_id)
        data = self._normalise_endpoint(payload, current["data"])
        data["status"] = current["data"].get("status", STATUS_DRAFT)
        data["contract_version"] = CONTRACT_VERSION
        data["published_at"] = current["data"].get("published_at")
        data["automation_count"] = len(self.automations(room_id))
        return self.store.update(current["id"], data, actor=actor, source=source)

    def publish(
        self,
        room_id: str,
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Step 5's **Publish**. The gate a delivery is turned away at.

        *"To publish workflows, users must have Publish permissions for
        workflows."* That is a different permission from the one that saved the
        endpoint, and the source names only Publish here - which is why Super Admin
        is not in :data:`PUBLISH_PERMISSIONS`. See the
        ``super-admin-does-not-imply-publish`` inference.
        """
        self._require(permissions, PUBLISH_PERMISSIONS, "publishing a workflow")
        current = self.require_endpoint(room_id)
        if current["data"].get("status") == STATUS_PUBLISHED:
            raise AlreadyPublished(
                f"the endpoint for room {room_id} is already published; {PUBLISH_RULE}"
            )
        return self.store.update(
            current["id"],
            {"status": STATUS_PUBLISHED, "published_at": _now()},
            actor=actor,
            source=source,
        )

    def unpublish(
        self,
        room_id: str,
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The off switch. Not retirement: the configuration and every delivery stay."""
        self._require(permissions, PUBLISH_PERMISSIONS, "unpublishing a workflow")
        current = self.require_endpoint(room_id)
        if current["data"].get("status") != STATUS_PUBLISHED:
            raise EndpointStateConflict(
                f"the endpoint for room {room_id} is not published, so there is "
                "nothing to unpublish"
            )
        return self.store.update(
            current["id"],
            {"status": STATUS_DRAFT, "published_at": None, "unpublished_at": _now()},
            actor=actor,
            source=source,
        )

    def sample(self, room_id: str) -> dict[str, Any]:
        """What the CRM's built-in **Test** control will send, and what it signs.

        The control itself lives in the CRM - there is no room-side button that can
        press it - so this is a preview, and the response says so. It exists
        because a rep comparing their CRM test send against the room's delivery log
        needs both to be the same bytes, and the signed canonical string is what
        makes that checkable rather than a guess.
        """
        endpoint = self.require_endpoint(room_id)
        data = endpoint["data"]
        deals = self.deals(room_id)
        body = sample_body(data, deal=deals[0] if deals else {})
        properties = body.get("properties") or {}
        rendered = json.dumps(properties, sort_keys=True, default=str)
        uri = uri_for(room_id)
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        secret = str((data.get("auth") or {}).get("secret") or "")
        return {
            "room_id": room_id,
            "is_preview": True,
            "note": (
                "The built-in Test control is a CRM-side control. This is the body "
                "and the signature this endpoint expects, for comparison against the "
                "test the rep sends from the CRM."
            ),
            "method": data.get("method"),
            "uri": uri,
            "query": {str(k): "" if v is None else str(v) for k, v in properties.items()},
            "body": body,
            "raw_body": rendered,
            "signature": {
                "timestamp": timestamp,
                "header": "x-hubspot-signature-v3",
                "value": sign(secret, "POST", uri, rendered, timestamp),
                "canonical_string": canonical_string("POST", uri, rendered, timestamp),
            },
            "auth": dict(data.get("auth") or {}),
            "body_mode_configured": (data.get("body") or {}).get("mode"),
            "body_keys": (data.get("body") or {}).get("keys") or [],
            "stage_key": data.get("stage_key"),
            "id_key": data.get("id_key"),
        }

    # -- automations (step 2, as the room records them) ---------------------- #

    def automations(self, room_id: str, *, include_retired: bool = False) -> list[dict[str, Any]]:
        """The CRM-side automations pointed at this room's endpoint.

        ``include_retired`` reaches the soft-deleted rows, which the plain listing
        leaves out. A retired automation is still named by the deliveries it fired,
        so a reader who wants to resolve one of those names needs it back.
        """
        rows = self.store.list(
            COLLECTION_AUTOMATION, room_id=room_id, limit=1000, include_deleted=include_retired
        )
        if include_retired:
            return rows
        return [row for row in rows if not row.get("deleted_at")]

    @staticmethod
    def _normalise_trigger(value: Any) -> dict[str, Any]:
        """A start condition, in the two forms the research gives.

        *"deal stage becomes 'Contract Sent'"* is a property equalling a value;
        *"or a contact property changes"* is a property changing. Both name a
        property, so a trigger with no property is refused - it is the one part of
        the CRM-side action this room can neither act on nor report.
        """
        raw = dict(value or {}) if isinstance(value, Mapping) else {}
        prop = str(raw.get("property") or "").strip()
        if not prop:
            raise InvalidTrigger(
                "a workflow start condition names a property - 'deal stage becomes "
                "\"Contract Sent\"', or 'a contact property changes'"
            )
        trigger: dict[str, Any] = {
            "property": prop,
            "property_name": raw.get("property_name"),
        }
        if raw.get("equals") not in (None, ""):
            trigger["kind"] = "becomes"
            trigger["equals"] = raw["equals"]
        elif raw.get("changed") or raw.get("kind") in ("changes", "changed"):
            trigger["kind"] = "changes"
        else:
            raise InvalidTrigger(
                f"start condition on {prop!r} says neither what it becomes nor that it "
                "changes; the researched conditions are 'becomes <value>' and 'property "
                "changes'"
            )
        return trigger

    def add_automation(
        self,
        room_id: str,
        payload: Mapping[str, Any],
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Step 2, recorded: one more CRM-side automation aimed at this endpoint.

        The 1,000-per-app cap is the sourced limit, counted across every room bound
        to the same App ID because the source counts per app. Refused, not clamped:
        a silently dropped 1,001st automation is one nobody can find.
        """
        self._require(permissions, SETUP_PERMISSIONS, "setting up a webhook action")
        endpoint = self.require_endpoint(room_id)
        data = endpoint["data"]
        body_payload = dict(payload or {})
        name = str(body_payload.get("name") or "").strip()
        if not name:
            raise InvalidRequest("a workflow needs a name for a rep to find it by")
        app_id = str(
            body_payload.get("app_id") or (data.get("auth") or {}).get("app_id") or room_id
        )
        used = self.subscriptions_used(app_id)
        if used >= SUBSCRIPTION_LIMIT_PER_APP:
            raise AutomationLimitReached(
                f"app {app_id} already has {used} webhook subscriptions; you can "
                f"create up to {SUBSCRIPTION_LIMIT_PER_APP:,} per app"
            )
        if "body" in body_payload:
            automation_body = self._normalise_body(body_payload["body"])
        elif body_payload.get("static_values"):
            # The researched editor adds a static value by typing a Key and a Value,
            # so that pair is the shape a caller naturally has.
            automation_body = self._normalise_body(
                {"mode": "customize", "keys": body_payload["static_values"]}
            )
        else:
            # No override at all, so the endpoint's own body mode applies - which is
            # what a workflow that customises nothing does in the CRM. Recorded as
            # the absence of an override rather than as a third body mode, because
            # the researched pair is closed.
            automation_body = {"mode": BODY_INHERIT, "keys": []}
        record = self.store.create(
            COLLECTION_AUTOMATION,
            {
                "name": name,
                "app_id": app_id,
                "object": data.get("object"),
                "trigger": self._normalise_trigger(body_payload.get("trigger")),
                "body": automation_body,
                "enabled": bool(body_payload.get("enabled", True)),
                "description": body_payload.get("description") or "",
                "endpoint_id": endpoint["id"],
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        self._recount(room_id, actor=actor, source=source)
        return record

    def retire_automation(
        self,
        room_id: str,
        automation_id: str,
        *,
        permissions: Any = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Retire one automation. A soft delete, so the deliveries naming it resolve.

        The same reason ``dsr.db.audited`` keeps the row: the delivery log names
        the automation that fired, and destroying it would leave that history
        pointing at nothing.
        """
        self._require(permissions, SETUP_PERMISSIONS, "changing a webhook action")
        record = self.store.get(automation_id)
        if record is None or record.get("collection") != COLLECTION_AUTOMATION:
            raise UnknownAutomation(f"automation {automation_id} is not registered")
        if record.get("room_id") != room_id or record.get("deleted_at"):
            raise UnknownAutomation(
                f"automation {automation_id} is not a live automation of room {room_id}"
            )
        result = self.store.delete(automation_id, actor=actor, source=source)
        self._recount(room_id, actor=actor, source=source)
        return result

    def _recount(self, room_id: str, *, actor: str | None, source: str) -> None:
        """Keep the endpoint's own count of automations honest.

        A denormalised counter is a claim the settings page makes out loud, and a
        claim that drifts is worse than no claim at all.
        """
        endpoint = self.endpoint(room_id)
        if endpoint is None:
            return
        count = len(self.automations(room_id))
        if endpoint["data"].get("automation_count") == count:
            return
        self.store.update(endpoint["id"], {"automation_count": count}, actor=actor, source=source)

    def subscriptions_used(self, app_id: str) -> int:
        """How many webhook subscriptions this app already carries, in any room.

        A dotted JSON path in the automation's own payload, resolved through the
        dynamic index, so this is a query rather than a scan - and it is keyed on
        the *app*, not the room, because that is where the sourced cap applies.
        """
        return self.store.count_where(COLLECTION_AUTOMATION, {"app_id": str(app_id)})

    # -- delivery (steps 5 and 6) -------------------------------------------- #

    def receive(
        self,
        room_id: str,
        *,
        method: str,
        uri: str,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        actor: str | None = None,
        source: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Step 6, whole: authenticate, resolve, update the panel, notify the rep.

        Two refusals write nothing, and the split is the point:

        * no endpoint at all - there is no URL a rep ever gave the CRM;
        * a request that failed authentication - the one input to this product not
          shown to be entitled to anything, and the row it would leave is
          indistinguishable from a real one to everyone who reads the log later.

        Everything past authentication is recorded whatever it does, because a rep
        whose automation reached a room and was turned away needs to find that out.
        """
        endpoint = self.require_endpoint(room_id)
        auth = endpoint["data"].get("auth") or {}
        is_get = method.strip().upper() == "GET"
        # A GET has no body, so there is nothing for a signature to cover; its
        # properties arrive in the query string, which is part of the URI.
        raw_body = "" if is_get else _raw_text(body)
        ok, reason = verify(
            auth,
            method=method,
            uri=uri,
            body=raw_body,
            headers=headers,
            query=query,
            now=now,
        )
        if not ok:
            raise UnauthenticatedDelivery(
                f"the request did not authenticate against this room's endpoint ({reason})"
            )
        try:
            return self._deliver(
                room_id,
                endpoint,
                method=method,
                uri=uri,
                query=query or {},
                body=body,
                actor=actor,
                source=source,
                now=now,
            )
        except WebhookError as exc:
            self._record_refusal(
                room_id,
                endpoint,
                method=method,
                uri=uri,
                body=body,
                raw_body=raw_body,
                reason=str(getattr(exc, "reason", "") or exc.code),
                error=exc.code,
                detail=str(exc),
                actor=actor,
                source=source,
                now=now,
            )
            raise

    def _deliver(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        *,
        method: str,
        uri: str,
        query: Mapping[str, Any],
        body: Any,
        actor: str | None,
        source: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """The whole second half of the researched flow, in one transaction.

        One transaction for the reason the vendor documentation gives: *"When a
        webhook is slow or times out, the workflow action may take longer than
        expected to execute"*. A delivery that half-applied would leave the deal
        panel showing something the delivery log does not describe, and the log is
        the thing this product promises is complete.
        """
        data = endpoint["data"]
        if data.get("status") != STATUS_PUBLISHED:
            raise EndpointNotPublished(
                f"the endpoint for room {room_id} was saved but never published, and {PUBLISH_RULE}"
            )
        is_get = method.strip().upper() == "GET"
        payload = read_payload(query if is_get else body, endpoint=data, version=CONTRACT_VERSION)
        if payload.object_declared and payload.object_declared != str(data.get("object")):
            raise ObjectMismatch(
                f"the endpoint is configured for {data.get('object')} and the payload is "
                f"about {payload.object_declared}"
            )
        if not payload.properties:
            raise EmptyPayload(
                "the payload carried no properties: **Include all [object] "
                "properties** and **Customize request body** both send at least one"
            )

        fingerprint = _fingerprint(uri, payload)
        kept = self._find_kept(room_id, fingerprint)
        if kept is not None:
            return self._record_duplicate(
                kept, room_id, method=method, actor=actor, source=source, now=now
            )

        # Retired automations are in the map too, marked as retired. A delivery
        # that names one was sent by something this room really did register, and
        # reporting it as unknown would be the log disagreeing with the automation
        # list for no reason a rep could act on.
        known: dict[str, str] = {}
        retired: set[str] = set()
        for row in self.store.list(
            COLLECTION_AUTOMATION, room_id=room_id, limit=1000, include_deleted=True
        ):
            name = str(row["data"].get("name"))
            known[name] = row["id"]
            if row.get("deleted_at") or not row["data"].get("enabled", True):
                retired.add(name)
        notes: list[str] = []
        if payload.missing_keys:
            notes.append("body_key_missing")
        automation_id = known.get(str(payload.automation)) if payload.automation else None
        if payload.automation and automation_id is None:
            # The endpoint is deliberately not an allow-list. The research is
            # explicit that any number of CRM-side automations can target it and
            # that it needs no per-workflow secret, so refusing a label this room
            # has not heard of would break the extensibility the sources promise.
            notes.append("automation_unknown")

        with self.store.db.transaction(actor=actor, source=source) as tx:
            deal, deal_created, stage_changed, changed_keys, stale_keys = self._apply(
                room_id, payload, endpoint=data, actor=actor, source=source, now=now, tx=tx
            )
            effect = _effect(payload, stage_changed, changed_keys)
            noticed = effect in (EFFECT_STAGE_CHANGED, EFFECT_PROPERTIES_ONLY)
            delivery = tx.create(
                COLLECTION_DELIVERY,
                {
                    "outcome": OUTCOME_ACCEPTED,
                    "reason": "",
                    "effect": effect,
                    "method": method.strip().upper(),
                    "uri": uri,
                    "object": data.get("object"),
                    "external_id": payload.external_id or (deal["data"].get("external_id")),
                    "id_source": payload.id_source,
                    "stage": payload.stage,
                    "stage_key": data.get("stage_key"),
                    "stage_source": payload.stage_source,
                    "previous_stage": deal["data"].get("previous_stage"),
                    "deal_id": deal["id"],
                    "deal_created": deal_created,
                    "changed_keys": changed_keys,
                    "stale_keys": stale_keys,
                    "delivery_id": payload.delivery_id,
                    "automation": payload.automation,
                    "automation_id": automation_id,
                    "automation_known": automation_id is not None or payload.automation is None,
                    "automation_retired": str(payload.automation) in retired,
                    "occurred_at": payload.occurred_at,
                    "fingerprint": fingerprint,
                    "duplicate_attempts": 0,
                    "body_mode_configured": (data.get("body") or {}).get("mode"),
                    "body_mode_observed": payload.body_mode_observed,
                    "missing_keys": list(payload.missing_keys),
                    "contract_version": payload.version,
                    "version_declared": payload.version_declared,
                    "notes": notes,
                    "notified": noticed,
                    "notice_id": None,
                    "payload": payload.to_dict(),
                    "at": _stamp(now),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            if noticed:
                notice = tx.create(
                    COLLECTION_NOTICE,
                    self._notice_data(room_id, deal, payload, effect, delivery["id"], now),
                    room_id=room_id,
                    actor=actor,
                    source=source,
                )
                tx.update(delivery["id"], {"notice_id": notice["id"]}, actor=actor, source=source)
                delivery = {**delivery, "data": {**delivery["data"], "notice_id": notice["id"]}}
        return self._delivery_report(delivery, endpoint, deal)

    def _apply(
        self,
        room_id: str,
        payload: Any,
        *,
        endpoint: Mapping[str, Any],
        actor: str | None,
        source: str,
        now: datetime | None,
        tx: Any,
    ) -> tuple[dict[str, Any], bool, bool, list[str], list[str]]:
        """ "Resolves the record", then updates the deal panel, on ``tx``.

        * an external id a deal in this room already carries wins;
        * a payload with no usable id in a room with exactly one deal is that
          deal - a single-deal room has no ambiguity to resolve;
        * a payload with an id no deal here carries creates that deal, because a
          signed delivery from this room's own endpoint is how a room learns about
          a deal it has not seen;
        * anything else is refused, because choosing between several deals by
          guessing is how a stage lands on the wrong buyer's panel.

        Every property is merged verbatim and nothing is dropped. A property a
        payload omitted is *stale*, not deleted: a customised body sends only the
        keys it names, so clearing the rest would erase the room's own data every
        time a rep trimmed their CRM-side body.
        """
        data = endpoint
        deal, created = self._resolve(room_id, payload)
        stored = dict(deal["data"]) if deal else {}
        previous_stage = stored.get("stage")
        incoming = dict(payload.properties)
        merged = {**stored.get("properties", {}), **incoming}
        changed = sorted(
            key
            for key, value in merged.items()
            if (stored.get("properties") or {}).get(key) != value
        )
        stale = sorted(set(stored.get("properties") or {}) - set(incoming))
        stage = payload.stage if payload.stage is not None else previous_stage
        stage_changed = stage is not None and str(stage) != str(previous_stage or "")
        patch: dict[str, Any] = {
            "object": data.get("object"),
            "external_id": payload.external_id or stored.get("external_id"),
            "id_source": payload.id_source or stored.get("id_source"),
            "stage": stage,
            "previous_stage": previous_stage if stage_changed else stored.get("previous_stage"),
            "stage_key": data.get("stage_key"),
            "stage_at": _stamp(now) if stage_changed else stored.get("stage_at"),
            "properties": merged,
            "changed_keys": changed,
            "stale_keys": stale,
        }
        if deal is None:
            deal = tx.create(COLLECTION_DEAL, patch, room_id=room_id, actor=actor, source=source)
        else:
            deal = tx.update(deal["id"], patch, actor=actor, source=source)
        return deal, created, stage_changed, changed, stale

    def _resolve(self, room_id: str, payload: Any) -> tuple[dict[str, Any] | None, bool]:
        existing = self.store.list(COLLECTION_DEAL, room_id=room_id, limit=1000)
        if payload.external_id:
            for record in existing:
                if str(record["data"].get("external_id")) == payload.external_id:
                    return record, False
            return None, True
        if len(existing) == 1:
            return existing[0], False
        raise UnresolvedDeal(
            "the payload carried no usable external id and this room has "
            f"{len(existing)} deal(s) to choose between; set the endpoint's id_key "
            "or add the key to the automation's customized body"
        )

    def _notice_data(
        self,
        room_id: str,
        deal: Mapping[str, Any],
        payload: Any,
        effect: str,
        delivery_id: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """The rep's half of the researched data flow.

        *"Maps payload onto the room model -> updates deal panel and notifies the
        rep."* One row per delivery that changed the panel, and none for a delivery
        that did not - see the ``quiet-when-nothing-changed`` inference.
        """
        data = deal["data"]
        room = self.store.get(room_id) or {}
        room_data = room.get("data") or {}
        who = room_data.get("account") or room_data.get("name") or room_id
        previous = data.get("previous_stage")
        stage = data.get("stage")
        if effect == EFFECT_STAGE_CHANGED and previous:
            summary = f"{who}: deal {data.get('external_id')} moved {previous} -> {stage}"
        elif effect == EFFECT_STAGE_CHANGED:
            summary = f"{who}: deal {data.get('external_id')} is now {stage}"
        else:
            summary = (
                f"{who}: {len(data.get('changed_keys') or [])} property change(s) on "
                f"the {data.get('object') or 'record'}"
            )
        return {
            "summary": summary,
            "effect": effect,
            "severity": "info",
            "room_id": room_id,
            "deal_id": deal["id"],
            "external_id": data.get("external_id"),
            "stage": stage,
            "previous_stage": previous,
            "changed_keys": data.get("changed_keys") or [],
            "delivery_id": delivery_id,
            "automation": payload.automation,
            "read": False,
            "read_at": None,
            "at": _stamp(now),
        }

    def _find_kept(self, room_id: str, fingerprint: str) -> dict[str, Any] | None:
        """The first live delivery with this fingerprint, if there is one.

        ``fingerprint`` is a JSON path in the delivery's own payload, so this is a
        query through the dynamic index rather than a scan of the room's log. The
        fingerprint is computed over the endpoint path as well as the payload, so
        two rooms can never collide on it.
        """
        for row in self.store.find(COLLECTION_DELIVERY, {"fingerprint": fingerprint}, limit=50):
            if row.get("room_id") == room_id and not row.get("deleted_at"):
                return row
        return None

    def _record_duplicate(
        self,
        kept: Mapping[str, Any],
        room_id: str,
        *,
        method: str,
        actor: str | None,
        source: str,
        now: datetime | None,
    ) -> dict[str, Any]:
        """A retry, answered 200, counted on the delivery that was kept.

        No second row: a CRM that retries after a timeout is normal, and a log
        holding the same event three times is a log nobody reads. The counter is
        what makes the retry visible - see the ``retry-is-a-counter-not-a-row``
        inference.
        """
        data = dict(kept["data"])
        attempts = int(data.get("duplicate_attempts") or 0) + 1
        updated = self.store.update(
            kept["id"],
            {
                "duplicate_attempts": attempts,
                "last_duplicate_at": _stamp(now),
                "last_duplicate_method": method.strip().upper(),
            },
            actor=actor,
            source=source,
        )
        report = dict(data)
        report.update(
            {
                "id": updated["id"],
                "revision": updated["revision"],
                "room_id": room_id,
                "outcome": OUTCOME_DUPLICATE,
                "reason": REASONS["duplicate_delivery"],
                "duplicate_attempts": attempts,
                "kept_delivery_id": kept["id"],
            }
        )
        return report

    def _record_refusal(
        self,
        room_id: str,
        endpoint: Mapping[str, Any],
        *,
        method: str,
        uri: str,
        body: Any,
        raw_body: str,
        reason: str,
        error: str,
        detail: str,
        actor: str | None,
        source: str,
        now: datetime | None,
    ) -> None:
        """Keep a delivery the endpoint authenticated and then turned away."""
        self.store.create(
            COLLECTION_DELIVERY,
            {
                "outcome": OUTCOME_REFUSED,
                "reason": reason,
                "error": error,
                "detail": detail,
                "effect": EFFECT_NONE,
                "method": method.strip().upper(),
                "uri": uri,
                "endpoint_id": endpoint["id"],
                "endpoint_status": endpoint["data"].get("status"),
                "at": _stamp(now),
                "notified": False,
                "raw_body": raw_body[:2000] or _raw_text(body)[:2000],
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _delivery_report(
        self,
        record: Mapping[str, Any],
        endpoint: Mapping[str, Any],
        deal: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        data = dict(record["data"])
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "outcome": data.get("outcome"),
            "effect": data.get("effect"),
            "reason": data.get("reason") or None,
            "notes": data.get("notes") or [],
            "contract_version": data.get("contract_version", CONTRACT_VERSION),
            "object": data.get("object"),
            "external_id": data.get("external_id"),
            "stage": data.get("stage"),
            "previous_stage": data.get("previous_stage"),
            "deal": _deal_view(deal),
            "deal_created": data.get("deal_created", False),
            "deal_id": data.get("deal_id"),
            "notified": data.get("notified", False),
            "notice_id": data.get("notice_id"),
            "duplicate_attempts": data.get("duplicate_attempts", 0),
            "body_mode_configured": data.get("body_mode_configured"),
            "body_mode_observed": data.get("body_mode_observed"),
            "missing_keys": data.get("missing_keys") or [],
            "changed_keys": data.get("changed_keys") or [],
            "stale_keys": data.get("stale_keys") or [],
            "automation": data.get("automation"),
            "automation_known": data.get("automation_known"),
            "automation_retired": data.get("automation_retired", False),
            "endpoint_status": endpoint["data"].get("status"),
            "delivery": data,
        }

    # -- reads ---------------------------------------------------------------- #

    def deliveries(
        self,
        room_id: str,
        *,
        outcome: str | None = None,
        effect: str | None = None,
        external_id: str | None = None,
        include_refused: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The room's delivery log, newest first, every refusal included.

        Refused deliveries are in the list by default: they are the rows a rep
        reads when their automation reached a room and nothing happened, and
        hiding them is what makes an integration look broken with no explanation
        on screen. ``include_refused=false`` is there for a reader who wants only
        the ones that landed.

        Each filter is a key in the delivery's own JSON payload, so a field a team
        added is filterable the moment it is written - no migration, no change to
        this route. The room is applied by the list itself, which is a column,
        and the filters on top of it are applied here so that a limit means "this
        room's latest N" rather than "N from anywhere in the product". The window
        the filters run over is the room's newest 1,000 deliveries, which is the
        store's own per-query ceiling; a room with more than that keeps the
        history in the audit log and stops serving it from this list.
        """
        rows = self.store.list(COLLECTION_DELIVERY, room_id=room_id, limit=1000)
        if not include_refused:
            rows = [row for row in rows if row["data"].get("outcome") != OUTCOME_REFUSED]
        selected = []
        for row in rows:
            data = row["data"]
            if outcome and data.get("outcome") != outcome:
                continue
            if effect and data.get("effect") != effect:
                continue
            if external_id and str(data.get("external_id")) != str(external_id):
                continue
            selected.append(dict(data, id=row["id"], room_id=row.get("room_id")))
        return selected[: max(1, int(limit))]

    def delivery(self, room_id: str, delivery_id: str) -> dict[str, Any] | None:
        for row in self.deliveries(room_id, limit=1000):
            if row["id"] == delivery_id:
                return row
        return None

    def deals(self, room_id: str) -> list[dict[str, Any]]:
        """The deal panel, newest first."""
        return [
            _deal_view(record)
            for record in self.store.list(COLLECTION_DEAL, room_id=room_id, limit=1000)
        ]

    def deal(self, room_id: str, deal_ref: str) -> dict[str, Any] | None:
        """One deal, by its room record id or by the CRM's external id."""
        for record in self.store.list(COLLECTION_DEAL, room_id=room_id, limit=1000):
            data = record["data"]
            if record["id"] == deal_ref or str(data.get("external_id")) == str(deal_ref):
                return _deal_view(record)
        return None

    def notices(
        self, room_id: str, *, unread_only: bool = False, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = self.store.list(COLLECTION_NOTICE, room_id=room_id, limit=1000)
        return [
            dict(row["data"], id=row["id"], room_id=row.get("room_id"))
            for row in rows
            if not (unread_only and row["data"].get("read"))
        ][: max(1, int(limit))]

    def acknowledge(
        self,
        room_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Mark notices read, by id or all of them.

        *"Notifies the rep"* is half a feature if a notification cannot be dealt
        with. Acknowledging is a write, so it is audited under its own route rather
        than folded into a read.
        """
        body = dict(payload or {})
        ids = body.get("notice_ids")
        wanted = [str(item) for item in ids] if isinstance(ids, (list, tuple)) else None
        everything = bool(body.get("all"))
        if wanted is None and not everything:
            raise InvalidRequest("name the notices to acknowledge, or pass all=true")
        marked = 0
        for row in self.store.list(COLLECTION_NOTICE, room_id=room_id, limit=1000):
            if row["data"].get("read"):
                continue
            if not everything and row["id"] not in (wanted or []):
                continue
            self.store.update(
                row["id"], {"read": True, "read_at": _now()}, actor=actor, source=source
            )
            marked += 1
        return {
            "room_id": room_id,
            "acknowledged": marked,
            "unread": sum(1 for row in self.notices(room_id) if not row.get("read")),
        }

    def summary(self, room_id: str) -> dict[str, Any]:
        """Counts for the room's header, and the researched constraints beside them."""
        endpoint = self.endpoint(room_id)
        data = endpoint["data"] if endpoint else {}
        auth = data.get("auth") or {}
        app_id = str(auth.get("app_id") or room_id)
        log = self.deliveries(room_id, limit=1000)
        outcomes: dict[str, int] = {}
        effects: dict[str, int] = {}
        for row in log:
            key = str(row.get("outcome") or "unknown")
            outcomes[key] = outcomes.get(key, 0) + 1
            key = str(row.get("effect") or EFFECT_NONE)
            effects[key] = effects.get(key, 0) + 1
        notices = self.notices(room_id, limit=1000)
        return {
            "room_id": room_id,
            "endpoint": {
                "registered": endpoint is not None,
                "status": data.get("status"),
                "method": data.get("method"),
                "object": data.get("object"),
                "url": data.get("url"),
                "path": uri_for(room_id),
                "auth_mode": auth.get("mode"),
                "app_id": auth.get("app_id"),
                "body_mode": (data.get("body") or {}).get("mode"),
                "id_key": data.get("id_key"),
                "stage_key": data.get("stage_key"),
                "published_at": data.get("published_at"),
            },
            "automations": len(self.automations(room_id)),
            "subscriptions_used": self.subscriptions_used(app_id),
            "subscription_limit": SUBSCRIPTION_LIMIT_PER_APP,
            "deliveries": len(log),
            "by_outcome": outcomes,
            "by_effect": effects,
            "duplicate_attempts": sum(int(row.get("duplicate_attempts") or 0) for row in log),
            "deals": len(self.deals(room_id)),
            "notices": len(notices),
            "unread": sum(1 for row in notices if not row.get("read")),
            "rate_limit_note": (
                "Webhook calls made via workflows do not count towards the API rate "
                "limit, so this endpoint applies no inbound quota."
            ),
            "slow_sender_note": (
                "HubSpot regulates webhook traffic separately; when a webhook is slow or "
                "times out the workflow action takes longer. Every delivery here is one "
                "transaction and opens no socket."
            ),
        }


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")


def _raw_text(body: Any) -> str:
    """The exact bytes a signature covers.

    A body that arrived as a string is signed as that string; a body that arrived
    as a mapping is signed as the JSON the sender must have produced, with sorted
    keys. Without the sort, a room re-serialising a body whose keys arrived in a
    different order would refuse a request that was signed correctly - a failure
    that would only ever appear against a real CRM.
    """
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    return json.dumps(body, sort_keys=True, default=str)


def _fingerprint(uri: str, payload: Any) -> str:
    """What makes two deliveries the same delivery.

    The declared delivery id when the automation carries one - and the research's
    **Customize request body** is exactly how a rep adds a static value, so a
    static ``delivery_id`` is a supported way to make a retry unambiguous rather
    than an invention. Otherwise the body itself: the same properties to the same
    endpoint are the same event, which is what makes a timeout-driven retry
    idempotent with no vendor support at all.
    """
    if payload.delivery_id:
        material = f"id:{payload.delivery_id}"
    else:
        material = json.dumps(
            {"object": payload.object_declared, "properties": payload.properties},
            sort_keys=True,
            default=str,
        )
    return hashlib.sha256(f"{uri}\n{material}".encode("utf-8")).hexdigest()


def _effect(payload: Any, stage_changed: bool, changed_keys: Sequence[str]) -> str:
    """What a delivery did to the panel, and therefore whether it is worth a notice.

    One rule, no per-object special cases: a notice goes out when the panel
    changed and stays quiet when it did not.
    """
    if payload.stage is not None:
        return EFFECT_STAGE_CHANGED if stage_changed else EFFECT_STAGE_UNCHANGED
    return EFFECT_PROPERTIES_ONLY if changed_keys else EFFECT_NONE


def _deal_view(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "object": data.get("object"),
        "external_id": data.get("external_id"),
        "stage": data.get("stage"),
        "previous_stage": data.get("previous_stage"),
        "stage_key": data.get("stage_key"),
        "stage_at": data.get("stage_at"),
        "properties": data.get("properties") or {},
        "changed_keys": data.get("changed_keys") or [],
        "stale_keys": data.get("stale_keys") or [],
        "updated_at": record.get("updated_at"),
    }
