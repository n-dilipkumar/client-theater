"""The DSR fragment catalogue, and the rules for configuring a fragment.

What is sourced and what is not
-------------------------------
The research for WF-002 (``docs/research/digital-sales-room-workflows/wf/WF-002.md``)
documents three out-of-the-box fragment sets and the fragments inside them, and it
enumerates the configuration fields for some of those fragments. It does **not**
enumerate the fields for every fragment.

So this module encodes exactly what was documented:

* ``Document Gallery Block`` - "Document 1 through Document 4" selectors that
  "take one file from the room's documents, the same files listed in the room's
  Documents view", and the documented limit: "Document Gallery Block has a fixed
  set of four document selectors instead of the *Number of Items* field. To show
  more than four documents on a page, add another Document Gallery Block for
  each additional set of four."
* ``Timeline Block`` - "*Number of Steps* sets how many steps appear (default
  four), and *Current Step* marks how far the deal has progressed."
* ``Video Block`` - "Under *Video Options*, URL points to the video and Width
  and Height set the player's dimensions. Autoplay is off by default."
* ``PDF Preview Block`` - selects one document; "the editable link that selects
  the PDF appears only in the page editor", so buyers see the rendered preview
  but not the selector.
* The three accessibility hooks the docs call out as meant to be edited: Page
  Bar's *Header Image Alt Description*, Sidebar's *Sidebar ARIA Label*, and
  Vertical Navigation's *ARIA Label*.

Every other fragment carries ``documented_fields: False`` and an empty field
list. That is not an omission in this implementation: inventing a field schema
for those fragments would be a design inference dressed as a specification, and
it would break the project's own rule that an unsourced capability is a
hypothesis. Those fragments are still fully usable - the configuration panel
offers a free-form JSON editor, and any key a team adds is stored and preserved
verbatim.

Where to the catalogue lives
----------------------------
The three shipped sets are code constants here; a third party's set arrives as
``fragment_set`` and ``fragment`` records in the audited store, and
:func:`fragment_catalogue` returns the two merged. That was decided with Jev
(``code_plus_db_merge``, confidence 0.97, recorded in
``orchestration/decisions/jev-audit.jsonl``) rather than guessed.

Shapes
------
A field is::

    {
      "key": "number_of_steps",       # config key, arbitrary to the caller
      "label": "Number of Steps",     # shown in the configuration panel
      "type": "number",               # see FIELD_TYPES
      "default": 4,                   # present ONLY where a default is documented
      "min": 1, "max": 12,            # numbers only
      "options": [...],               # selects only
      "help": "...",                  # one line of guidance
      "aria": True,                   # documented accessibility hook
    }

A fragment is::

    {
      "key": "timeline",              # stable identifier, referenced by every block
      "name": "Timeline Block",
      "set": "digital-sales-room",    # key of the owning fragment set
      "summary": "...",
      "icon": "timeline",             # name of an icon in the frontend icon set
      "source": "shipped",            # "shipped" or "custom"
      "documented_fields": True,
      "fields": [ ...field... ],
    }

Both are plain JSON because they are the wire format: the frontend reads them
over HTTP and a custom set is written through the same API shape.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

FIELD_TYPES = ("text", "textarea", "number", "boolean", "url", "document", "select")

#: ``document_1`` .. ``document_4`` are the four selectors the vendor documents.
#: The pattern exists so a fifth slot is rejected with the documented remedy
#: rather than silently stored.
DOCUMENT_SLOT_RE = re.compile(r"^document_(\d+)$")

#: The Document Gallery Block's documented fixed number of selectors.
DOCUMENT_GALLERY_SLOTS = 4

_OWNERSHIP_HELP = "Takes one file from the room's documents, the same files listed in the room's Documents view."


def _document_slot(position: int) -> dict[str, Any]:
    return {"key": f"document_{position}", "label": f"Document {position}", "type": "document", "help": _OWNERSHIP_HELP}


def _analytics_fragment(key: str, name: str, summary: str, icon: str) -> dict[str, Any]:
    """One Analytics-set fragment.

    The research names all ten Analytics fragments but documents no field list
    for any of them, so each is declared with an open field list and is
    configured through the free-form editor.
    """
    return {
        "key": key,
        "name": name,
        "set": "digital-sales-room-analytics",
        "summary": summary,
        "icon": icon,
        "documented_fields": False,
        "fields": [],
    }


# --------------------------------------------------------------------------- #
# Shipped fragment sets
# --------------------------------------------------------------------------- #

FRAGMENT_SETS: list[dict[str, Any]] = [
    {
        "key": "digital-sales-room",
        "name": "Digital Sales Room",
        "summary": "Buyer-facing content blocks. This is the set a room's pages are built from.",
        "source": "shipped",
    },
    {
        "key": "digital-sales-room-analytics",
        "name": "Digital Sales Room Analytics",
        "summary": "Engagement blocks. Use them to build a custom view of a room's engagement data.",
        "source": "shipped",
    },
    {
        "key": "dsr-fragments",
        "name": "DSR Fragments",
        "summary": "Console chrome: page bar, sidebar, sidebar trigger, vertical navigation.",
        "source": "shipped",
    },
]


# --------------------------------------------------------------------------- #
# Shipped fragments
# --------------------------------------------------------------------------- #

FRAGMENTS: list[dict[str, Any]] = [
    # -- Digital Sales Room (11) --------------------------------------------- #
    {
        "key": "document-gallery",
        "name": "Document Gallery Block",
        "set": "digital-sales-room",
        "summary": "Shows documents from the room's documents folder.",
        "icon": "document-gallery",
        "documented_fields": True,
        "fields": [
            _document_slot(1),
            _document_slot(2),
            _document_slot(3),
            _document_slot(4),
        ],
        "note": (
            "Four fixed document selectors. To show more than four documents on a page, add another "
            "Document Gallery Block for each additional set of four."
        ),
    },
    {
        "key": "gallery",
        "name": "Gallery Block",
        "set": "digital-sales-room",
        "summary": "A grid of images.",
        "icon": "gallery",
        # The vendor documents a "Number of Items" field but not which fragment
        # it belongs to or what it defaults to, so no field is declared here.
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "header-main",
        "name": "Header Main",
        "set": "digital-sales-room",
        "summary": "The room header. It also renders the notice shown when the room is archived.",
        "icon": "header-main",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "header-user",
        "name": "Header User",
        "set": "digital-sales-room",
        "summary": "The signed-in user's header.",
        "icon": "header-user",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "our-team",
        "name": "Our Team Block",
        "set": "digital-sales-room",
        "summary": "Introduces the people the buyer is working with.",
        "icon": "our-team",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "pdf-preview",
        "name": "PDF Preview Block",
        "set": "digital-sales-room",
        "summary": "Previews a PDF from the room's documents.",
        "icon": "pdf-preview",
        "documented_fields": True,
        "fields": [
            {
                "key": "document",
                "label": "Document",
                "type": "document",
                "help": _OWNERSHIP_HELP,
            }
        ],
        "note": "The editable link that selects the PDF appears only in the page editor.",
    },
    {
        "key": "question-and-answer",
        "name": "Question and Answer Block",
        "set": "digital-sales-room",
        "summary": "Questions the buyer can raise against the room.",
        "icon": "question-and-answer",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "text",
        "name": "Text Block",
        "set": "digital-sales-room",
        "summary": "Free text content.",
        "icon": "text",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "timeline",
        "name": "Timeline Block",
        "set": "digital-sales-room",
        "summary": "A numbered sequence of steps, each with a secondary line for an estimate.",
        "icon": "timeline",
        "documented_fields": True,
        "fields": [
            {
                "key": "number_of_steps",
                "label": "Number of Steps",
                "type": "number",
                "default": 4,
                "min": 1,
                "max": 20,
                "help": "Sets how many steps appear (default four).",
            },
            {
                "key": "current_step",
                "label": "Current Step",
                "type": "number",
                "default": 0,
                "min": 0,
                "max": 20,
                "help": "Marks how far the deal has progressed.",
            },
        ],
    },
    {
        "key": "video",
        "name": "Video Block",
        "set": "digital-sales-room",
        "summary": "A video player.",
        "icon": "video",
        "documented_fields": True,
        "fields": [
            {
                "key": "url",
                "label": "URL",
                "type": "url",
                "help": "Points to the video.",
            },
            {
                "key": "width",
                "label": "Width",
                "type": "number",
                "min": 1,
                "help": "Sets the player's width in pixels.",
            },
            {
                "key": "height",
                "label": "Height",
                "type": "number",
                "min": 1,
                "help": "Sets the player's height in pixels.",
            },
            {
                "key": "autoplay",
                "label": "Autoplay",
                "type": "boolean",
                "default": False,
                "help": "Autoplay is off by default.",
            },
        ],
    },
    {
        "key": "welcome",
        "name": "Welcome Block",
        "set": "digital-sales-room",
        "summary": "The buyer's welcome message.",
        "icon": "welcome",
        "documented_fields": False,
        "fields": [],
    },

    # -- Digital Sales Room Analytics (10) ----------------------------------- #
    _analytics_fragment("activity-log", "Activity Log", "A log of room activity.", "activity-log"),
    _analytics_fragment(
        "documents-statistics", "Documents Statistics", "Statistics for the room's documents.", "documents-statistics"
    ),
    _analytics_fragment("engagement-chart", "Engagement Chart", "Engagement over time.", "engagement-chart"),
    _analytics_fragment("frequency-chart", "Frequency Chart", "How often the room is visited.", "frequency-chart"),
    _analytics_fragment("latest-activity", "Latest Activity", "The most recent buyer actions.", "latest-activity"),
    _analytics_fragment(
        "most-active-visitors", "Most Active Visitors", "Individuals ranked by their total actions.", "most-active-visitors"
    ),
    _analytics_fragment("navigation", "Navigation", "Navigation between analytics blocks.", "navigation"),
    _analytics_fragment("room-general", "Room General", "General room information.", "room-general"),
    _analytics_fragment("room-statistics", "Room Statistics", "View time, visits, visitors and actions.", "room-statistics"),
    _analytics_fragment("room-trend", "Room Trend", "Engagement health: cold, warm or hot.", "room-trend"),

    # -- DSR Fragments (4) --------------------------------------------------- #
    {
        "key": "page-bar",
        "name": "Page Bar",
        "set": "dsr-fragments",
        "summary": "The bar across the top of a page.",
        "icon": "page-bar",
        "documented_fields": True,
        "fields": [
            {
                "key": "header_image_alt_description",
                "label": "Header Image Alt Description",
                "type": "text",
                "aria": True,
                "help": "Alt text for the header image. Read by screen readers.",
            }
        ],
    },
    {
        "key": "sidebar",
        "name": "Sidebar",
        "set": "dsr-fragments",
        "summary": "The editing sidebar.",
        "icon": "sidebar",
        "documented_fields": True,
        "fields": [
            {
                "key": "sidebar_aria_label",
                "label": "Sidebar ARIA Label",
                "type": "text",
                "aria": True,
                "help": "Accessible name for the sidebar landmark.",
            }
        ],
    },
    {
        "key": "sidebar-trigger",
        "name": "Sidebar Trigger",
        "set": "dsr-fragments",
        "summary": "The control that opens the sidebar.",
        "icon": "sidebar-trigger",
        "documented_fields": False,
        "fields": [],
    },
    {
        "key": "vertical-navigation",
        "name": "Vertical Navigation",
        "set": "dsr-fragments",
        "summary": "The vertical navigation rail.",
        "icon": "vertical-navigation",
        "documented_fields": True,
        "fields": [
            {
                "key": "aria_label",
                "label": "ARIA Label",
                "type": "text",
                "aria": True,
                "help": "Accessible name for the navigation landmark.",
            }
        ],
    },
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class FragmentError(ValueError):
    """A fragment key or a fragment configuration that cannot be accepted.

    Raised as a ``ValueError`` so the HTTP layer turns it into a 400 with the
    message intact; the message is written for the person editing the page.
    """


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def _record_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return dict(record.get("data") or {})


def normalise_set(payload: Mapping[str, Any], source: str) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise FragmentError("a fragment set needs a non-empty 'key'")
    return {
        "key": key,
        "name": str(payload.get("name") or key),
        "summary": str(payload.get("summary") or ""),
        "source": source,
    }


def normalise_fragment(payload: Mapping[str, Any], source: str) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise FragmentError("a fragment needs a non-empty 'key'")
    set_key = str(payload.get("set") or "").strip()
    if not set_key:
        raise FragmentError(f"fragment {key!r} must name the 'set' it belongs to")
    fields = [normalise_field(key, field) for field in (payload.get("fields") or [])]
    return {
        "key": key,
        "name": str(payload.get("name") or key),
        "set": set_key,
        "summary": str(payload.get("summary") or ""),
        "icon": str(payload.get("icon") or "schema"),
        "note": str(payload.get("note") or ""),
        "source": source,
        "documented_fields": bool(payload.get("documented_fields", bool(fields))),
        "fields": fields,
    }


def normalise_field(fragment_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip()
    if not key:
        raise FragmentError(f"a field of fragment {fragment_key!r} needs a non-empty 'key'")
    field_type = str(payload.get("type") or "text")
    if field_type not in FIELD_TYPES:
        raise FragmentError(
            f"field {fragment_key}.{key} has unknown type {field_type!r}; expected one of {', '.join(FIELD_TYPES)}"
        )
    field: dict[str, Any] = {
        "key": key,
        "label": str(payload.get("label") or key),
        "type": field_type,
        "help": str(payload.get("help") or ""),
        "aria": bool(payload.get("aria", False)),
    }
    if "default" in payload:
        field["default"] = payload["default"]
    for bound in ("min", "max"):
        if payload.get(bound) is not None:
            field[bound] = payload[bound]
    if field_type == "select":
        options = payload.get("options")
        if not isinstance(options, Sequence) or isinstance(options, (str, bytes)) or not options:
            raise FragmentError(f"select field {fragment_key}.{key} needs a non-empty 'options' list")
        field["options"] = [str(option) for option in options]
    return field


def fragment_catalogue(
    custom_sets: Iterable[Mapping[str, Any]] = (),
    custom_fragments: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Merge the shipped catalogue with a team's own sets and fragments.

    ``custom_sets`` and ``custom_fragments`` are store *records*; anything they
    carry beyond the documented keys is preserved on the returned definition,
    because a team is allowed to attach its own metadata to a fragment set
    without a migration.

    A custom definition whose key collides with a shipped one is rejected rather
    than merged: silently overriding a shipped fragment would make the built-in
    catalogue depend on write order.
    """
    sets: dict[str, dict[str, Any]] = {}
    fragments: dict[str, dict[str, Any]] = {}

    for payload in FRAGMENT_SETS:
        entry = normalise_set(payload, "shipped")
        sets[entry["key"]] = entry
    for payload in FRAGMENTS:
        entry = normalise_fragment(payload, "shipped")
        fragments[entry["key"]] = entry

    for record in custom_sets:
        payload = _record_payload(record)
        entry = normalise_set(payload, "custom")
        if entry["key"] in sets and sets[entry["key"]]["source"] == "shipped":
            raise FragmentError(
                f"fragment set {entry['key']!r} is shipped; a custom set must use its own key"
            )
        if entry["key"] in sets:
            entry["record_id"] = record.get("id")
        sets[entry["key"]] = entry

    for record in custom_fragments:
        payload = _record_payload(record)
        entry = normalise_fragment(payload, "custom")
        if entry["key"] in fragments and fragments[entry["key"]]["source"] == "shipped":
            raise FragmentError(
                f"fragment {entry['key']!r} is shipped; a custom fragment must use its own key"
            )
        if entry["set"] not in sets:
            raise FragmentError(
                f"fragment {entry['key']!r} names set {entry['set']!r}, which does not exist"
            )
        if entry["key"] in fragments:
            entry["record_id"] = record.get("id")
        fragments[entry["key"]] = entry

    ordered_sets = sorted(sets.values(), key=lambda item: (item["source"] != "shipped", item["key"]))
    ordered_fragments = sorted(
        fragments.values(), key=lambda item: (item["set"], item["key"])
    )
    for entry in ordered_sets:
        entry["fragments"] = [f["key"] for f in ordered_fragments if f["set"] == entry["key"]]

    return {
        "sets": ordered_sets,
        "fragments": ordered_fragments,
        "counts": {
            "sets": len(ordered_sets),
            "fragments": len(ordered_fragments),
            "shipped_sets": sum(1 for s in ordered_sets if s["source"] == "shipped"),
            "shipped_fragments": sum(1 for f in ordered_fragments if f["source"] == "shipped"),
        },
    }


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def find_fragment(catalogue: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    for fragment in catalogue.get("fragments", []):
        if fragment["key"] == key:
            return fragment
    return None


def default_config(fragment: Mapping[str, Any]) -> dict[str, Any]:
    """The configuration a freshly placed fragment starts with.

    Only fields with a *documented* default get a value. Everything else starts
    absent rather than guessed, so nothing on a published page can be mistaken
    for something the vendor specified.
    """
    config: dict[str, Any] = {}
    for field in fragment.get("fields", []):
        if "default" in field:
            config[field["key"]] = field["default"]
    return config


def _coerce_number(fragment_key: str, field: Mapping[str, Any], value: Any) -> float | int:
    # bool is a subclass of int in Python; a boolean is never a valid number here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FragmentError(
            f"{fragment_key}.{field['key']} must be a number, got {type(value).__name__}"
        )
    minimum, maximum = field.get("min"), field.get("max")
    if minimum is not None and value < minimum:
        raise FragmentError(f"{fragment_key}.{field['key']} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise FragmentError(f"{fragment_key}.{field['key']} must be at most {maximum}, got {value}")
    return int(value) if float(value).is_integer() else value


def _coerce_text(fragment_key: str, field: Mapping[str, Any], value: Any) -> str:
    if not isinstance(value, str):
        raise FragmentError(
            f"{fragment_key}.{field['key']} must be text, got {type(value).__name__}"
        )
    return value


def _coerce_url(fragment_key: str, field: Mapping[str, Any], value: Any) -> str:
    text = _coerce_text(fragment_key, field, value).strip()
    if text and not text.lower().startswith(("http://", "https://")):
        raise FragmentError(
            f"{fragment_key}.{field['key']} must be an http(s) URL, got {text!r}"
        )
    return text


def normalise_config(
    fragment: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    *,
    document_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate a fragment's configuration, preserving undeclared keys.

    Declared fields are checked and coerced. Undeclared keys are the schema
    flexibility promise in action: a team that adds ``campaign_id`` to a Text
    Block must not need a code change, a migration, or our permission, so those
    keys pass through untouched.
    """
    if config is None:
        config = {}
    if not isinstance(config, Mapping):
        raise FragmentError("fragment configuration must be a JSON object")

    known = {field["key"]: field for field in fragment.get("fields", [])}
    fragment_key = str(fragment.get("key", "fragment"))
    known_ids = set(document_ids)

    result = {str(key): value for key, value in config.items()}

    # The Document Gallery Block's limit is documented, so it is enforced here
    # with the documented remedy rather than left to the reader.
    if fragment_key == "document-gallery":
        for key in result:
            match = DOCUMENT_SLOT_RE.match(key)
            if match and int(match.group(1)) > DOCUMENT_GALLERY_SLOTS:
                raise FragmentError(
                    f"Document Gallery Block has a fixed set of {DOCUMENT_GALLERY_SLOTS} document "
                    f"selectors, so {key!r} is not one. To show more than {DOCUMENT_GALLERY_SLOTS} "
                    f"documents on a page, add another Document Gallery Block for each additional "
                    f"set of {DOCUMENT_GALLERY_SLOTS}."
                )

    for key, value in result.items():
        field = known.get(key)
        if field is None:
            continue
        if value is None or value == "":
            continue
        kind = field["type"]
        if kind == "number":
            result[key] = _coerce_number(fragment_key, field, value)
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise FragmentError(
                    f"{fragment_key}.{key} must be true or false, got {type(value).__name__}"
                )
        elif kind in ("text", "textarea"):
            result[key] = _coerce_text(fragment_key, field, value)
        elif kind == "url":
            result[key] = _coerce_url(fragment_key, field, value)
        elif kind == "select":
            text = _coerce_text(fragment_key, field, value)
            if text not in field.get("options", []):
                raise FragmentError(
                    f"{fragment_key}.{key} must be one of {', '.join(field.get('options', []))}, got {text!r}"
                )
            result[key] = text
        elif kind == "document":
            reference = _coerce_text(fragment_key, field, value).strip()
            # "Takes one file from the room's documents." A room with no
            # documents therefore accepts no selector value at all, which is
            # why this is not guarded on a non-empty id set.
            if reference and reference not in known_ids:
                raise FragmentError(
                    f"{fragment_key}.{key} points at {reference!r}, which is not a document in this room"
                )
            result[key] = reference

    return result


def referenced_documents(block: Mapping[str, Any], fragment: Mapping[str, Any] | None) -> list[str]:
    """Document ids a block points at, in declared field order.

    ``fragment`` is the catalogue definition. When it is missing - a fragment
    from a set that has since been removed - the ``document_*`` slot pattern is
    still honoured so a buyer view never drops a document reference it can see.
    """
    config = block.get("config") or {}
    fields = (fragment or {}).get("fields") or []
    document_fields = [field["key"] for field in fields if field["type"] == "document"]

    ids: list[str] = []
    for key in document_fields:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            ids.append(value.strip())
    for key, value in config.items():
        if key in document_fields or not isinstance(value, str) or not value.strip():
            continue
        if DOCUMENT_SLOT_RE.match(key):
            ids.append(value.strip())
    return ids
