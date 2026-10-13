"""The researched vocabulary for WF-104, and nothing else.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-104.md`` and section 1 of
``docs/research/raw/automation-engagement.md``, from the six sources the research cites.

What the research pins down
---------------------------

* **Four block kinds and no others.** "A Series is made up of 'blocks'. There are four
  kinds of block" - rule, content, wait, tag. :data:`BLOCK_KINDS` is that closed set.
* **The block names in the user flow.** A content block is "(Post/Chat/Email/Tour/Push/
  Carousel/Banner)", which is :data:`CONTENT_TYPES`.
* **The four channels the data flow names.** "message dispatched to a channel (in-app post,
  email, mobile push, SMS)", which is :data:`CHANNELS`.
* **The send window is a retry window, not a delay.** "For each message block, you can define
  how long a message should *try* to send for, as your customers might not be online to
  receive it right away."
* **The offline branch is a timeout, not a delivery failure.** The tooltip reads "If the
  person still hasn't come online after 30d, they will leave this path".
* **Two rule wait modes.** "'Try to match for x time' means it will wait **up to that amount
  of time** for the person to match the rule." and "'Try to match once' means that if the
  person does not match the rules **as soon as they reach that block** they will leave the
  path".
* **The evaluation is a poll.** "Series checks every 30 minutes whether a customer matches
  each rule, message, wait, or tag block." :data:`POLL_INTERVAL_SECONDS` is 30 minutes and
  :data:`POLL_QUOTE` carries the sentence.
* **Two engagement-event lists.** The webhook topic line gives
  :data:`CONTENT_STAT_EVENTS`; the data flow adds ``open``, ``click`` and ``reply``. Which
  list is canonical is recorded by :data:`EVENT_CANONICAL_SOURCE`, not left to a reader.
* **The export obligation.** "Series can be exported (``GET /export/workflows/{id}``) for the
  EU Data Act", and the export exposes :data:`EXPORT_FIELDS`.

What the research leaves open
-----------------------------

**The filter fields.** The data sources name "Intercom Contact object + ``custom_attributes``"
and "Company object (for company predicates)", but publish no field list. :data:`CONTACT_ATTRIBUTES`
and :data:`COMPANY_ATTRIBUTES` are this build's vocabulary, each row flagged ``sourced`` or
marked as an inference, and both are exported so WF-105 can reuse the same names.

**The audience is caller-supplied.** :data:`AUDIENCE_MECHANISM` records the mechanism the
domain module uses to obtain the contacts it filters. Chosen by Jev, audit
``jev-20261013T150622-7908-82240``, at confidence 0.86.

A rule with a published vocabulary is a rule a client can send, and a client that sends the
wrong one gets a message naming the published set. That is the whole reason these tuples are
exported rather than inlined at their call sites.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections this workflow owns
# --------------------------------------------------------------------------- #

#: One row per nurture series. A series is a draft until it is set live.
SERIES_COLLECTION = "wf104_series"

#: One row per buyer that entered a series. Carries the cursor and the hold state.
RUN_COLLECTION = "wf104_run"

#: One row per channel attempt, including the fallback attempt.
DISPATCH_COLLECTION = "wf104_dispatch"

#: One row per engagement event, recorded against the buyer.
ENGAGEMENT_COLLECTION = "wf104_engagement"

#: One row per event on a run, so a page can show the walk in order.
ACTIVITY_COLLECTION = "wf104_activity"

# --------------------------------------------------------------------------- #
# The four block kinds, and no others
# --------------------------------------------------------------------------- #

#: The closed set, quoted: "A Series is made up of 'blocks'. There are four kinds of block".
BLOCK_KINDS: tuple[str, ...] = ("rule", "content", "wait", "tag")

BLOCK_LABELS: dict[str, str] = {
    "rule": "Rule block",
    "content": "Content block",
    "wait": "Wait block",
    "tag": "Tag block",
}

#: The block kind the user flow drops first to set who enters: step 2, "Drag a **rule block**
#: onto the canvas and set the entry filters".
ENTRY_BLOCK_KIND = "rule"

# --------------------------------------------------------------------------- #
# The two channels' worth of content: the content block's own types and the delivery channels
# --------------------------------------------------------------------------- #

#: The content-block types, quoted from the user flow: "(Post/Chat/Email/Tour/Push/Carousel/
#: Banner)".
CONTENT_TYPES: tuple[str, ...] = (
    "post",
    "chat",
    "email",
    "tour",
    "push",
    "carousel",
    "banner",
)

CONTENT_TYPE_LABELS: dict[str, str] = {
    "post": "Post",
    "chat": "Chat",
    "email": "Email",
    "tour": "Tour",
    "push": "Push",
    "carousel": "Carousel",
    "banner": "Banner",
}

#: The delivery channels, quoted from the data flow: "message dispatched to a channel (in-app
#: post, email, mobile push, SMS)". ``mobile_push`` is the research's "mobile push" and
#: ``sms`` its "SMS"; ``in_app`` is its "in-app post".
CHANNELS: tuple[str, ...] = ("in_app", "email", "mobile_push", "sms")

CHANNEL_LABELS: dict[str, str] = {
    "in_app": "In-app post",
    "email": "Email",
    "mobile_push": "Mobile push",
    "sms": "SMS",
}

#: The fallback channel the user flow names: step 5, "Add a fallback **email** block on the
#: *If not online after 30d* branch". Email is the only fallback channel in the flow.
FALLBACK_CHANNEL_DEFAULT = "email"

# --------------------------------------------------------------------------- #
# The send window: a retry window, not a delay
# --------------------------------------------------------------------------- #

SEND_WINDOW_QUOTE = (
    "For each message block, you can define how long a message should *try* to send for, "
    "as your customers might not be online to receive it right away."
)

#: The label the research puts on the setting: a Post block's "Wait up to 30d".
SEND_WINDOW_LABEL = "Wait up to 30d"

#: True, and load-bearing: a send window retries, it does not sleep. A build that treated it
#: as a delay would hold a message until day 30, which is the opposite of the research.
SEND_WINDOW_IS_RETRY = True

#: Thirty days, the number in the researched label and in the tooltip. One default, one place.
DEFAULT_SEND_WINDOW_SECONDS = 30 * 24 * 60 * 60

#: The offline branch is a timeout, not a delivery failure. Quoted, from the tooltip on the
#: same setting: "If the person still hasn't come online after 30d, they will leave this path".
OFFLINE_BRANCH_QUOTE = "If the person still hasn't come online after 30d, they will leave this path"
OFFLINE_BRANCH_IS_TIMEOUT = True

#: The reason code the offline branch records when it fires.
OFFLINE_FALLBACK_REASON = "not_online_after_send_window"

# --------------------------------------------------------------------------- #
# Rule blocks: two wait modes
# --------------------------------------------------------------------------- #

#: The two modes, quoted. ``for`` waits up to a matched amount of time; ``once`` leaves the
#: path as soon as the person reaches the block without matching.
RULE_MATCH_MODES: tuple[str, ...] = ("for", "once")

RULE_MATCH_MODE_LABELS: dict[str, str] = {
    "for": "Try to match for a time",
    "once": "Try to match once",
}

RULE_MATCH_FOR_QUOTE = (
    "'Try to match for x time' means it will wait **up to that amount of time** for the "
    "person to match the rule."
)
RULE_MATCH_ONCE_QUOTE = (
    "'Try to match once' means that if the person does not match the rules **as soon as they "
    "reach that block** they will leave the path"
)

#: The practised example from step 6: "a rule block on *When delivered -> Clicked link* with
#: ``Try to match for 3d``". Three days, in seconds.
DEFAULT_MATCH_WITHIN_SECONDS = 3 * 24 * 60 * 60

#: The condition a non-entry rule block waits for, named as the user flow names it.
RULE_CONDITIONS: tuple[str, ...] = ("delivered", "opened", "clicked_link", "replied", "goal")

RULE_CONDITION_LABELS: dict[str, str] = {
    "delivered": "When delivered",
    "opened": "When opened",
    "clicked_link": "When clicked a link",
    "replied": "When replied",
    "goal": "When the goal is met",
}

#: The engagement event each rule condition reads. A rule condition is the seller's word; the
#: event is the machine value the webhook topic fires.
CONDITION_EVENTS: dict[str, str] = {
    "delivered": "receipt",
    "opened": "open",
    "clicked_link": "click",
    "replied": "reply",
    "goal": "goal_success",
}

# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #

#: The poll interval, quoted: "Series checks every 30 minutes whether a customer matches each
#: rule, message, wait, or tag block." Thirty minutes, in seconds.
POLL_INTERVAL_SECONDS = 30 * 60

POLL_QUOTE = (
    "Series checks every 30 minutes whether a customer matches each rule, message, wait, or "
    "tag block."
)

# --------------------------------------------------------------------------- #
# The engagement event vocabulary
# --------------------------------------------------------------------------- #

#: The events the ``content_stat.series`` webhook topic fires, quoted:
#: "receipt · goal_success · series_completion · series_disengagement · series_exit".
CONTENT_STAT_EVENTS: tuple[str, ...] = (
    "receipt",
    "goal_success",
    "series_completion",
    "series_disengagement",
    "series_exit",
)

#: The events the data flow names that the topic line does not: "engagement stat events
#: (`receipt`, `open`, `click`, `goal_success`, `reply`)". ``receipt`` and ``goal_success``
#: appear in both lists.
DATA_FLOW_EVENTS: tuple[str, ...] = ("open", "click", "reply")

#: Every event this workflow accepts: the topic's set plus the data flow's two extras.
ENGAGEMENT_EVENTS: tuple[str, ...] = CONTENT_STAT_EVENTS + (
    "open",
    "click",
    "reply",
)

EVENT_LABELS: dict[str, str] = {
    "receipt": "Delivered (receipt)",
    "goal_success": "Goal met",
    "series_completion": "Series completed",
    "series_disengagement": "Series disengaged",
    "series_exit": "Left the series",
    "open": "Opened",
    "click": "Clicked a link",
    "reply": "Replied",
}

#: Which list is canonical, recorded rather than left to the reader. The webhook topic is the
#: machine contract for a series' lifecycle, so it is canonical for the series events; the
#: data flow's ``open``, ``click`` and ``reply`` are canonical for a buyer's interactions.
EVENT_CANONICAL_SOURCE = (
    "content_stat.series webhook topic for series lifecycle events; the data flow for the "
    "per-buyer open, click and reply interaction events"
)

#: The events this workflow records against a buyer. The series-lifecycle events are terminal
#: states of a run rather than per-message interactions, so they are held separately.
INTERACTION_EVENTS: tuple[str, ...] = ("open", "click", "reply")

#: The series-lifecycle events, which move a run rather than an engagement count.
SERIES_LIFECYCLE_EVENTS: tuple[str, ...] = CONTENT_STAT_EVENTS

# --------------------------------------------------------------------------- #
# Series and run states
# --------------------------------------------------------------------------- #

SERIES_STATES: tuple[str, ...] = ("draft", "live")
STATE_DRAFT = "draft"
STATE_LIVE = "live"

RUN_STATES: tuple[str, ...] = ("running", "waiting", "finished", "exited")
RUN_RUNNING = "running"
RUN_WAITING = "waiting"
RUN_FINISHED = "finished"
RUN_EXITED = "exited"

RUN_STATE_LABELS: dict[str, str] = {
    "running": "Running",
    "waiting": "Waiting",
    "finished": "Finished",
    "exited": "Left the path",
}

# --------------------------------------------------------------------------- #
# The filter fields - the shared rule-block vocabulary WF-105 reuses
# --------------------------------------------------------------------------- #

#: The operators a filter may use. ``eq``/``neq`` compare as values, the four comparisons are
#: numeric, ``contains`` is list membership or a substring, ``in`` is a list of allowed
#: values, and ``exists`` is a presence test.
FILTER_OPERATORS: tuple[str, ...] = (
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "in",
    "exists",
)

#: The contact attributes a rule block may filter on. Each path resolves inside a contact's
#: ``attributes`` object. The ``sourced`` column carries the research sentence the field is
#: read from; a field whose sourcing is this build's is marked ``inference``.
CONTACT_ATTRIBUTES: tuple[dict[str, str], ...] = (
    {
        "path": "email",
        "label": "Email address",
        "kind": "string",
        "sourced": "Intercom Contact object",
    },
    {"path": "name", "label": "Full name", "kind": "string", "sourced": "Intercom Contact object"},
    {
        "path": "created_at",
        "label": "Signed up",
        "kind": "instant",
        "sourced": "Intercom Contact object",
    },
    {
        "path": "last_seen_at",
        "label": "Last seen",
        "kind": "instant",
        "sourced": "Intercom Contact object",
    },
    {"path": "plan", "label": "Plan", "kind": "string", "sourced": "custom_attributes"},
    {"path": "lead_score", "label": "Lead score", "kind": "number", "sourced": "custom_attributes"},
    {"path": "owner_id", "label": "Owner", "kind": "string", "sourced": "custom_attributes"},
    {"path": "locale", "label": "Locale", "kind": "string", "sourced": "custom_attributes"},
    {"path": "tags", "label": "Tags", "kind": "list", "sourced": "Tag table"},
    {"path": "segment_ids", "label": "Segments", "kind": "list", "sourced": "Segments"},
    {
        "path": "event_counts.click",
        "label": "Links clicked (count)",
        "kind": "number",
        "sourced": "event counts",
    },
    {
        "path": "event_counts.open",
        "label": "Messages opened (count)",
        "kind": "number",
        "sourced": "event counts",
    },
    {
        "path": "event_counts.reply",
        "label": "Replies (count)",
        "kind": "number",
        "sourced": "event counts",
    },
)

#: The company attributes a rule block may filter on. Each path resolves inside a contact's
#: ``company`` object and is addressed as ``company.<path>``. The research states the Company
#: object is used "for company predicates" and publishes no field list, so every row here is
#: an inference.
COMPANY_ATTRIBUTES: tuple[dict[str, str], ...] = (
    {"path": "name", "label": "Company name", "kind": "string", "sourced": "inference"},
    {"path": "industry", "label": "Industry", "kind": "string", "sourced": "inference"},
    {"path": "employees", "label": "Employees", "kind": "number", "sourced": "inference"},
    {"path": "country", "label": "Country", "kind": "string", "sourced": "inference"},
    {"path": "plan", "label": "Company plan", "kind": "string", "sourced": "inference"},
    {"path": "tags", "label": "Company tags", "kind": "list", "sourced": "inference"},
)

#: The prefix a company filter field carries. A filter field is either a bare contact path or
#: ``company.<path>``; :func:`filter_field_paths` returns both spellings.
COMPANY_FIELD_PREFIX = "company."

#: How the domain module obtains the contacts it filters, recorded as the decision it is.
#: Chosen by Jev, audit ``jev-20261013T150622-7908-82240``, at confidence 0.86.
AUDIENCE_MECHANISM = "caller_supplied_audience"

# --------------------------------------------------------------------------- #
# The export obligation
# --------------------------------------------------------------------------- #

#: What ``GET /export/workflows/{id}`` exposes, quoted from the research: "`GET
#: /export/workflows/{id}` also exposes `targeting`, `snapshot`, `attributes`, `embedded_rules`".
EXPORT_FIELDS: tuple[str, ...] = ("targeting", "snapshot", "attributes", "embedded_rules")

EXPORT_QUOTE = (
    "Content blocks can include Apps from the Intercom App Store; a Webhook block can be "
    "dropped onto the canvas to POST/PUT buyer data to any external URL; Series can be "
    "exported (GET /export/workflows/{id}) for the EU Data Act; GET /export/workflows/{id} "
    "also exposes targeting, snapshot, attributes, embedded_rules."
)

# --------------------------------------------------------------------------- #
# The user flow, clause by clause
# --------------------------------------------------------------------------- #

USER_FLOW: tuple[dict[str, str], ...] = (
    {
        "n": "1",
        "id": "new_series",
        "detail": "Go to Outbound > Series and click + New Series; pick Start from scratch or a template.",
        "route": "POST /api/wf-104/rooms/{room_id}/series",
    },
    {
        "n": "2",
        "id": "entry_rule",
        "detail": "Drag a rule block onto the canvas and set the entry filters (Who will enter).",
        "route": "GET /api/wf-104/rooms/{room_id}/series/{series_id}/preview",
    },
    {
        "n": "3",
        "id": "content_goal_scheduling",
        "detail": "Drag a content block, write the copy, and set a per-block Goal and Scheduling.",
        "route": "POST /api/wf-104/rooms/{room_id}/series",
    },
    {
        "n": "4",
        "id": "send_window",
        "detail": "Set the block's send window (Wait up to 30d) so it is retried while the buyer is offline.",
        "route": "POST /api/wf-104/rooms/{room_id}/runs/{run_id}/advance",
    },
    {
        "n": "5",
        "id": "fallback_email",
        "detail": "Add a fallback email block on the If not online after 30d branch.",
        "route": "POST /api/wf-104/rooms/{room_id}/runs/{run_id}/advance",
    },
    {
        "n": "6",
        "id": "rule_clicked_link",
        "detail": "Add a rule block on When delivered -> Clicked link with Try to match for 3d.",
        "route": "POST /api/wf-104/rooms/{room_id}/runs/{run_id}/resolve",
    },
    {
        "n": "7",
        "id": "converge",
        "detail": "Wire the divergent branches to converge on a shared next block (e.g. a product tour).",
        "route": "POST /api/wf-104/rooms/{room_id}/runs/{run_id}/advance",
    },
    {
        "n": "8",
        "id": "go_live",
        "detail": "Click Set series live.",
        "route": "POST /api/wf-104/rooms/{room_id}/series/{series_id}/go-live",
    },
)

#: The data flow, clause by clause.
DATA_FLOW: tuple[dict[str, str], ...] = (
    {
        "n": "1",
        "stage": "attributes",
        "detail": "Contact attributes / company attributes / event counts",
    },
    {"n": "2", "stage": "rule_gate", "detail": "rule-block filter evaluation"},
    {"n": "3", "stage": "entry", "detail": "block entry"},
    {
        "n": "4",
        "stage": "dispatch",
        "detail": "message dispatched to a channel (in-app post, email, mobile push, SMS)",
    },
    {
        "n": "5",
        "stage": "engagement",
        "detail": "engagement stat events (receipt, open, click, goal_success, reply) emitted back",
    },
    {"n": "6", "stage": "re_evaluate", "detail": "next rule block re-evaluates"},
    {"n": "7", "stage": "score", "detail": "engagement score / segment membership updates"},
    {"n": "8", "stage": "further_blocks", "detail": "further blocks"},
)


# --------------------------------------------------------------------------- #
# Validators a client can call to fail a value by name
# --------------------------------------------------------------------------- #


def require_block_kind(kind: Any) -> str:
    """Normalise and validate a block kind against the published four."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(kind or "").strip().lower().replace("-", "_")
    if value not in BLOCK_KINDS:
        raise SeriesRefused(
            f"block kind {kind!r} is not one of the published four: {', '.join(BLOCK_KINDS)}",
            code="unknown_block_kind",
            errors={"kind": "not a published block kind"},
        )
    return value


def require_channel(channel: Any) -> str:
    """Normalise and validate a delivery channel against the published four."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(channel or "").strip().lower().replace("-", "_")
    if value not in CHANNELS:
        raise SeriesRefused(
            f"channel {channel!r} is not one of the published channels: {', '.join(CHANNELS)}",
            code="unknown_channel",
            errors={"channel": "not a published channel"},
        )
    return value


def require_content_type(content_type: Any) -> str:
    """Normalise and validate a content-block type against the published seven."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(content_type or "").strip().lower().replace("-", "_")
    if value not in CONTENT_TYPES:
        raise SeriesRefused(
            f"content type {content_type!r} is not one of the published types: "
            f"{', '.join(CONTENT_TYPES)}",
            code="unknown_content_type",
            errors={"content_type": "not a published content type"},
        )
    return value


def require_rule_match_mode(mode: Any) -> str:
    """Normalise and validate a rule block's wait mode against the published two."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(mode or "").strip().lower()
    if value not in RULE_MATCH_MODES:
        raise SeriesRefused(
            f"rule match mode {mode!r} is not one of the published two: "
            f"{', '.join(RULE_MATCH_MODES)}",
            code="unknown_rule_match_mode",
            errors={"wait_mode": "not a published rule match mode"},
        )
    return value


def require_rule_condition(condition: Any) -> str:
    """Normalise and validate a rule block's condition against the published five."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(condition or "").strip().lower().replace("-", "_")
    if value not in RULE_CONDITIONS:
        raise SeriesRefused(
            f"rule condition {condition!r} is not one of the published conditions: "
            f"{', '.join(RULE_CONDITIONS)}",
            code="unknown_rule_condition",
            errors={"condition": "not a published rule condition"},
        )
    return value


def require_filter_operator(operator: Any) -> str:
    """Normalise and validate a filter operator against the published nine."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(operator or "").strip().lower()
    if value not in FILTER_OPERATORS:
        raise SeriesRefused(
            f"filter operator {operator!r} is not one of the published operators: "
            f"{', '.join(FILTER_OPERATORS)}",
            code="unknown_filter_operator",
            errors={"op": "not a published filter operator"},
        )
    return value


def require_engagement_event(event: Any) -> str:
    """Normalise and validate an engagement event against the published set."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(event or "").strip().lower().replace("-", "_")
    if value not in ENGAGEMENT_EVENTS:
        raise SeriesRefused(
            f"engagement event {event!r} is not one of the published events: "
            f"{', '.join(ENGAGEMENT_EVENTS)}",
            code="unknown_engagement_event",
            errors={"event": "not a published engagement event"},
        )
    return value


def filter_field_paths() -> tuple[str, ...]:
    """Every filter field path, contact paths first, then ``company.<path>``.

    Exported so WF-105 and the page's field picker read the same list this validator
    uses. A field it does not know is refused by :func:`require_filter_field` rather
    than accepted and never matched.
    """

    return tuple(row["path"] for row in CONTACT_ATTRIBUTES) + tuple(
        COMPANY_FIELD_PREFIX + row["path"] for row in COMPANY_ATTRIBUTES
    )


def filter_fields() -> list[dict[str, str]]:
    """Every filter field as a row, with its scope named, for a client's picker."""

    rows = [dict(row, scope="contact") for row in CONTACT_ATTRIBUTES]
    rows.extend(
        dict(row, path=COMPANY_FIELD_PREFIX + row["path"], scope="company")
        for row in COMPANY_ATTRIBUTES
    )
    return rows


def require_filter_field(field: Any) -> str:
    """Normalise and validate a filter field against the published vocabulary."""

    from dsr.nurture_series.errors import SeriesRefused

    value = str(field or "").strip()
    if value not in filter_field_paths():
        raise SeriesRefused(
            f"filter field {field!r} is not in the published vocabulary; "
            f"known fields: {', '.join(filter_field_paths())}",
            code="unknown_filter_field",
            errors={"field": "not a published filter field"},
        )
    return value


# --------------------------------------------------------------------------- #
# The published vocabulary and the failure codes
# --------------------------------------------------------------------------- #


def series_failures() -> dict[str, dict[str, Any]]:
    """Every refusal this workflow can return, by published reason code.

    A refusal with no published code is a refusal a client has to read prose to act on, so
    every one of these is named, and each carries the sentence it comes from where the
    research has one. Served by the router at ``GET /api/wf-104/vocabulary`` so the page
    cannot drift from the rules behind it.
    """

    return {
        "series_name_required": {
            "status": 400,
            "summary": "A series needs a name; it is what the Outbound > Series list shows.",
            "sourced": "Go to Outbound > Series and click + New Series.",
        },
        "no_blocks": {
            "status": 400,
            "summary": "A series needs at least one block; a series with no blocks does nothing.",
            "sourced": "A Series is made up of 'blocks'.",
        },
        "unknown_block_kind": {
            "status": 400,
            "summary": "The block kind is not one of the published four: rule, content, wait, tag.",
            "sourced": "There are four kinds of block - rule / content / wait / tag.",
        },
        "unknown_channel": {
            "status": 400,
            "summary": "The channel is not one of the published four.",
            "sourced": "message dispatched to a channel (in-app post, email, mobile push, SMS)",
        },
        "unknown_content_type": {
            "status": 400,
            "summary": "The content type is not one of the published seven.",
            "sourced": "Drag a content block (Post/Chat/Email/Tour/Push/Carousel/Banner).",
        },
        "unknown_rule_match_mode": {
            "status": 400,
            "summary": "A rule block's wait mode is for or once.",
            "sourced": "Try to match for x time / Try to match once.",
        },
        "unknown_rule_condition": {
            "status": 400,
            "summary": "The rule condition is not one of the published five.",
            "sourced": "a rule block on When delivered -> Clicked link",
        },
        "unknown_filter_operator": {
            "status": 400,
            "summary": "The filter operator is not one of the published nine.",
            "sourced": "rule-block filter evaluation",
        },
        "unknown_filter_field": {
            "status": 400,
            "summary": "The filter field is not in the published contact/company vocabulary.",
            "sourced": "Contact attributes / company attributes / event counts",
        },
        "bad_block": {
            "status": 400,
            "summary": "A block is malformed: it is not an object, or a value it needs is missing.",
            "sourced": "n/a",
        },
        "bad_audience": {
            "status": 400,
            "summary": "The audience is not a list of contacts, or a contact has no contact_id.",
            "sourced": "Contact attributes / company attributes / event counts",
        },
        "unknown_series_state": {
            "status": 400,
            "summary": "A series state is not one of the published two: draft, live.",
            "sourced": "draft until it is set live",
        },
        "unknown_run_state": {
            "status": 400,
            "summary": "A run state is not one of the published four.",
            "sourced": "n/a",
        },
        "bad_filter": {
            "status": 400,
            "summary": "A filter needs a field, a known operator, and a value for that operator.",
            "sourced": "n/a",
        },
        "match_within_required": {
            "status": 400,
            "summary": "A rule block in the 'for' mode needs how long to try, in seconds.",
            "sourced": RULE_MATCH_FOR_QUOTE,
        },
        "send_window_required": {
            "status": 400,
            "summary": "A content block with a fallback needs a send window to retry for.",
            "sourced": SEND_WINDOW_QUOTE,
        },
        "duration_out_of_range": {
            "status": 400,
            "summary": "A duration must be a positive number of seconds within its bound.",
            "sourced": "n/a",
        },
        "series_not_found": {"status": 404, "summary": "No such series.", "sourced": "n/a"},
        "run_not_found": {"status": 404, "summary": "No such run.", "sourced": "n/a"},
        "decision_not_found": {
            "status": 404,
            "summary": "No recorded design decision with that id.",
            "sourced": "n/a",
        },
        "series_already_live": {
            "status": 409,
            "summary": "The series is already live.",
            "sourced": "Click Set series live.",
            "remedy": "Read the series to see its state.",
        },
        "series_deleted": {
            "status": 409,
            "summary": "A deleted series cannot be edited or set live.",
            "sourced": "n/a",
            "remedy": "Create a new series.",
        },
        "run_not_advancing": {
            "status": 409,
            "summary": "The run is waiting or has finished, so it cannot be advanced.",
            "sourced": "n/a",
            "remedy": "Resolve the wait, or read the run to see its state.",
        },
        "run_not_waiting": {
            "status": 409,
            "summary": "The run is not waiting, so there is nothing to resolve.",
            "sourced": "n/a",
            "remedy": "Advance the run.",
        },
        "wait_not_elapsed": {
            "status": 409,
            "summary": "The wait window has not elapsed yet.",
            "sourced": "it will wait up to that amount of time for the person to match the rule",
            "remedy": "Resolve again once the window has elapsed.",
        },
        "unknown_engagement_event": {
            "status": 400,
            "summary": "The engagement event is not one of the published set.",
            "sourced": CONTENT_STAT_EVENTS[0] + ", ...",
        },
    }


def published_vocabulary() -> dict[str, Any]:
    """Everything a client needs to drive this workflow, in one response.

    A client renders its block picker, its filter builder, its channel picker and its status
    labels from this, and its error handling from ``series_failures``, so the terms a caller
    sends and the terms it branches on cannot drift apart.
    """

    return {
        "collections": {
            "series": SERIES_COLLECTION,
            "run": RUN_COLLECTION,
            "dispatch": DISPATCH_COLLECTION,
            "engagement": ENGAGEMENT_COLLECTION,
            "activity": ACTIVITY_COLLECTION,
        },
        "block_kinds": list(BLOCK_KINDS),
        "block_labels": dict(BLOCK_LABELS),
        "content_types": list(CONTENT_TYPES),
        "content_type_labels": dict(CONTENT_TYPE_LABELS),
        "channels": list(CHANNELS),
        "channel_labels": dict(CHANNEL_LABELS),
        "fallback_channel_default": FALLBACK_CHANNEL_DEFAULT,
        "series_states": list(SERIES_STATES),
        "state_draft": STATE_DRAFT,
        "state_live": STATE_LIVE,
        "run_states": list(RUN_STATES),
        "run_state_labels": dict(RUN_STATE_LABELS),
        "rule_match_modes": list(RULE_MATCH_MODES),
        "rule_match_mode_labels": dict(RULE_MATCH_MODE_LABELS),
        "rule_conditions": list(RULE_CONDITIONS),
        "rule_condition_labels": dict(RULE_CONDITION_LABELS),
        "condition_events": dict(CONDITION_EVENTS),
        "rule_match_for_quote": RULE_MATCH_FOR_QUOTE,
        "rule_match_once_quote": RULE_MATCH_ONCE_QUOTE,
        "default_match_within_seconds": DEFAULT_MATCH_WITHIN_SECONDS,
        "send_window": {
            "label": SEND_WINDOW_LABEL,
            "quote": SEND_WINDOW_QUOTE,
            "is_retry": SEND_WINDOW_IS_RETRY,
            "default_seconds": DEFAULT_SEND_WINDOW_SECONDS,
        },
        "offline_branch": {
            "quote": OFFLINE_BRANCH_QUOTE,
            "is_timeout": OFFLINE_BRANCH_IS_TIMEOUT,
            "reason": OFFLINE_FALLBACK_REASON,
        },
        "poll": {"interval_seconds": POLL_INTERVAL_SECONDS, "quote": POLL_QUOTE},
        "engagement": {
            "events": list(ENGAGEMENT_EVENTS),
            "event_labels": dict(EVENT_LABELS),
            "content_stat_events": list(CONTENT_STAT_EVENTS),
            "data_flow_events": list(DATA_FLOW_EVENTS),
            "interaction_events": list(INTERACTION_EVENTS),
            "series_lifecycle_events": list(SERIES_LIFECYCLE_EVENTS),
            "canonical_source": EVENT_CANONICAL_SOURCE,
        },
        "filter": {
            "operators": list(FILTER_OPERATORS),
            "fields": filter_fields(),
            "contact_attributes": [dict(row) for row in CONTACT_ATTRIBUTES],
            "company_attributes": [dict(row) for row in COMPANY_ATTRIBUTES],
            "shared_with": "WF-105 reuses this rule-block vocabulary",
        },
        "audience_mechanism": AUDIENCE_MECHANISM,
        "export": {"fields": list(EXPORT_FIELDS), "quote": EXPORT_QUOTE},
        "user_flow": [dict(step) for step in USER_FLOW],
        "data_flow": [dict(step) for step in DATA_FLOW],
        "seed_states": "mixed; see the seeder output",
        "series_failures": series_failures(),
        "sent_by_this_product": False,
        "channel_send_is_recorded": True,
        "note": (
            "No message leaves this product. Every dispatch row carries "
            "sent_by_this_product: false and channel_send_is_recorded: true, the same "
            "standard dsr.conversation_chase and dsr.automation_engagement follow."
        ),
    }
