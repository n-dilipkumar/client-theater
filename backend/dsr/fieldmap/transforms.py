"""Named, versioned transforms, resolved by name at run time.

The research fixes the extension point, not the list: "Transforms are named,
versioned functions registered in the connector (e.g. ``email.normalize``,
``picklist.map``, ``date.iso8601``), so a deployment can add a transform without
touching the sync engine." So the shape here is a registry keyed by
``name@version``, the sync engine resolves by name, and adding a transform is
registering one - no engine edit.

Four transforms are named in the research, by example or by transform:
``email.normalize`` (a lowercase email), ``date.iso8601`` (an ISO-8601 date),
``picklist.map`` (a picklist label mapped to an internal option value) and number
coercion. ``identity`` and ``text.trim`` are this build's addition: a mapping row
whose value needs no change still has to name a transform, and an empty string is
a worse answer than a named pass-through.

Resolution
----------

:meth:`Registry.resolve` takes an optional pinned version. A row that pins a
version gets exactly that one, so a mapping validated today does not change
meaning when a deployment registers a newer one; a row that pins nothing gets the
highest registered version. A pinned version that is not registered resolves to
``None``, which is a ``transform_unavailable`` error badge on the grid - never a
silent fallback to another version, because a mapping that quietly starts writing
different values is the defect this workflow exists to prevent.

A transform whose code is not in this registry can still be *declared* as data -
see :mod:`dsr.fieldmap.mappings` - so a deployment can record the transform it
intends to add and have the grid say it is not yet executable, rather than
refusing the declaration and leaving the admin with no place to write it down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from dsr.fieldmap.vocabulary import ROOM_FIELD_TYPES

TransformFn = Callable[[Any, Mapping[str, Any], str], Any]

#: The version every built-in transform ships at. Bumped per transform when its
#: behaviour changes in a way a stored mapping could notice.
BUILTIN_VERSION = 1


class TransformUnavailable(LookupError):
    """A transform name (or pinned version) the registry does not carry.

    A ``LookupError`` rather than this package's :class:`~dsr.fieldmap.errors.FieldMapError`
    because it is not a request this layer refuses: the *grid* renders it as a
    per-row badge, and a domain error type would need a process-wide handler to
    travel the one route a validation report takes.
    """


@dataclass(frozen=True)
class Transform:
    """One named, versioned function, and the vocabulary it declares."""

    name: str
    version: int
    description: str
    #: The sales-room field types this transform is for. Empty means "any", which
    #: is how ``identity`` and ``text.trim`` say so without pretending otherwise.
    applies_to: tuple[str, ...] = ()
    #: The researched example this transform came from, quoted. Empty for the two
    #: this build added.
    source: str = ""
    fn: TransformFn | None = field(default=None, compare=False)

    @property
    def key(self) -> str:
        return transform_key(self.name, self.version)

    @property
    def executable(self) -> bool:
        return self.fn is not None

    def applies(self, room_type: str) -> bool:
        """Whether this transform is for a sales-room field of this type.

        A field type this build does not know about is accepted rather than
        refused. The vocabulary in :mod:`dsr.fieldmap.vocabulary` is this product's
        own, and a team adding a ``geo_point`` column must be able to keep using
        ``text.trim`` without a code change. A type the vocabulary *does* know and
        this transform is not for is a different case, and that one is reported.
        """
        if not self.applies_to or not room_type:
            return True
        needle = str(room_type).strip().lower()
        if needle not in ROOM_FIELD_TYPES:
            return True
        return needle in self.applies_to

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "key": self.key,
            "description": self.description,
            "applies_to": list(self.applies_to),
            "builtin": self.fn is not None,
            "source": self.source,
        }


def transform_key(name: str, version: int | None = None) -> str:
    """The registry key for a name and version: ``name@version``."""
    return f"{str(name or '').strip()}@{int(version) if version is not None else ''}"


# --------------------------------------------------------------------------- #
# The built-ins
# --------------------------------------------------------------------------- #

RESEARCHED = (
    'WF-035 research: "a transform (lowercase email, ISO-8601 date, picklist label -> '
    'internal option value, number coercion)".'
)

_ENUMERATION_RULE = (
    'WF-035 research, quoted: "When including enumeration properties, you must use internal '
    "names to set values.\" The label side of the table is the sales room's own wording; the "
    "value side is the CRM's internal option value."
)


def _identity(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    return value


def _text_trim(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    if not isinstance(value, str):
        return value
    return value.strip()


def _email_normalize(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    """Lowercase and strip. The researched "lowercase email".

    A non-string is returned untouched rather than coerced: the type check on the
    grid is what reports a number arriving at an email property, and a transform
    that quietly turned ``1042`` into ``"1042"`` would hide it.
    """
    if not isinstance(value, str):
        return value
    return value.strip().lower()


def _date_iso8601(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    """The researched "ISO-8601 date".

    Accepts a ``date``, a ``datetime`` (returned as its date, in whatever offset it
    already carries - this is a *date* transform, not an instant), or an ISO-8601
    string including a trailing ``Z``. Anything it cannot read raises ``ValueError``,
    which the preview reports against the row rather than swallowing.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError(f"date.iso8601 cannot read a {type(value).__name__}")
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an ISO-8601 date or datetime") from exc


def _datetime_utc(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    """Normalise an instant to UTC ISO-8601 with a ``Z`` suffix.

    Not named in the research, which says "ISO-8601 date" - but a sales room that
    syncs engagement timestamps needs the instant half too, and shipping it as a
    separate, versioned transform is exactly the extension point the research
    describes. Naive input is read as UTC and says so in its own docstring, rather
    than being shifted by the server's local zone.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not an ISO-8601 datetime") from exc
    else:
        raise ValueError(f"datetime.utc cannot read a {type(value).__name__}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number_coerce(value: Any, _config: Mapping[str, Any], _direction: str) -> Any:
    """The researched "number coercion".

    A number passes through. A string is read; anything with a thousands separator
    or a currency symbol is not guessed at, because a CRM silently receiving ``0``
    for a revenue column is worse than a refusal. A boolean is refused outright: in
    Python ``True`` is an ``int``, and ``float(True)`` quietly becoming ``1.0`` is
    how a "yes" turns into a number.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("number.coerce will not read a boolean; map it to an enumeration instead")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{value!r} is not a number") from exc
    raise ValueError(f"number.coerce cannot read a {type(value).__name__}")


def _picklist_map(value: Any, config: Mapping[str, Any], direction: str) -> Any:
    """The researched "picklist label -> internal option value".

    The same table serves both directions, and which side is checked depends on
    which way the value is travelling - the rule is symmetric:

    * **out** - the key is the sales room's label and the result is what the CRM is
      sent, so the *value* side must be an internal option value.
    * **in** - the key is the CRM's internal option value and the result is the
      sales room's label, so the *key* side must be an internal option value.

    An unmapped value is returned untouched rather than dropped, so the preview
    shows exactly what would be sent; the grid is what refuses it, because only the
    grid can see the CRM's option set.
    """
    table = config.get("map")
    if not isinstance(table, Mapping) or not table:
        raise ValueError("picklist.map needs a non-empty `map` of {from: to} pairs")
    text = str(value).strip() if value is not None else ""
    if text not in table:
        return value
    return table[text]


BUILTINS: tuple[Transform, ...] = (
    Transform(
        name="identity",
        version=BUILTIN_VERSION,
        description="Pass the sales-room value through unchanged. The sync key uses it.",
        fn=_identity,
    ),
    Transform(
        name="text.trim",
        version=BUILTIN_VERSION,
        description="Strip leading and trailing whitespace from a text value.",
        applies_to=("text", "url", "email"),
        fn=_text_trim,
    ),
    Transform(
        name="email.normalize",
        version=BUILTIN_VERSION,
        description="Lowercase and strip an email address.",
        applies_to=("email",),
        source=RESEARCHED,
        fn=_email_normalize,
    ),
    Transform(
        name="date.iso8601",
        version=BUILTIN_VERSION,
        description="Read a date, a datetime or an ISO-8601 string; write YYYY-MM-DD.",
        applies_to=("date", "datetime"),
        source=RESEARCHED,
        fn=_date_iso8601,
    ),
    Transform(
        name="datetime.utc",
        version=BUILTIN_VERSION,
        description=(
            "Read a datetime or an ISO-8601 string; write UTC ISO-8601 with a Z suffix. "
            "A naive input is read as UTC."
        ),
        applies_to=("datetime",),
        source="This build's addition; the research names 'ISO-8601 date' only.",
        fn=_datetime_utc,
    ),
    Transform(
        name="number.coerce",
        version=BUILTIN_VERSION,
        description=(
            "Coerce a numeric string to a number. Refuses a boolean and a number it cannot read, "
            "rather than writing 0 into a CRM column."
        ),
        applies_to=("number",),
        source=RESEARCHED,
        fn=_number_coerce,
    ),
    Transform(
        name="picklist.map",
        version=BUILTIN_VERSION,
        description=(
            "Map a sales-room picklist label to the CRM's internal option value, and back on the "
            "way in. The side that must hold internal names depends on the direction."
        ),
        applies_to=("enumeration",),
        source=_ENUMERATION_RULE,
        fn=_picklist_map,
    ),
)


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


class Registry:
    """``name@version`` to :class:`Transform`, and the rules for resolving one.

    A plain object rather than a module-level singleton so a test can register a
    deployment's transform without leaking it into the rest of the suite. The
    default registry is :data:`REGISTRY`; :func:`registry` is the one the feature
    and the seeder use.
    """

    def __init__(self, transforms: Iterable[Transform] = ()) -> None:
        self._items: dict[str, Transform] = {}
        for transform in transforms:
            self.register(transform)

    def register(self, transform: Transform) -> Transform:
        """Add or replace one transform. The whole extension point."""
        if not transform.name:
            raise ValueError("a transform needs a name")
        if int(transform.version) < 1:
            raise ValueError("a transform needs a version of 1 or more")
        # Replacing a built-in's code is legitimate - it is how a deployment
        # overrides one - so this is a plain assignment with no guard. What a
        # *declaration* may not do is claim an executable: `Transform.executable`
        # reads `fn is not None`, and a declaration never carries one, so a
        # mapping row can tell the two apart without a second source of truth.
        self._items[transform.key] = transform
        return transform

    def unregister(self, name: str, version: int) -> bool:
        """Remove one transform. Returns whether there was one to remove."""
        return self._items.pop(transform_key(name, version), None) is not None

    def __contains__(self, key: object) -> bool:
        return str(key) in self._items

    def get(self, key: str) -> Transform | None:
        return self._items.get(str(key))

    def resolve(self, name: str, version: int | None = None) -> Transform | None:
        """The transform for a name, optionally pinned to a version.

        ``None`` when the name is unknown, or when a pinned version is not
        registered. Never falls back to another version: see the module docstring.
        """
        text = str(name or "").strip()
        if not text:
            return None
        if version is not None:
            return self._items.get(transform_key(text, int(version)))
        versions = [
            transform for key, transform in self._items.items() if key.rsplit("@", 1)[0] == text
        ]
        if not versions:
            return None
        return max(versions, key=lambda transform: transform.version)

    def require(self, name: str, version: int | None = None) -> Transform:
        """:meth:`resolve`, raising :class:`TransformUnavailable` instead of ``None``."""
        transform = self.resolve(name, version)
        if transform is None:
            pinned = f" version {version}" if version is not None else ""
            raise TransformUnavailable(f"no transform named {name!r}{pinned} is registered")
        return transform

    def versions_of(self, name: str) -> tuple[int, ...]:
        """Every registered version of a name, ascending."""
        text = str(name or "").strip()
        return tuple(
            sorted(
                int(key.rsplit("@", 1)[1]) for key in self._items if key.rsplit("@", 1)[0] == text
            )
        )

    def latest(self) -> tuple[Transform, ...]:
        """One entry per name, at its highest registered version, name-ordered.

        This is what the picker renders. An older pinned version stays resolvable
        and stays valid; it just is not offered, because offering a superseded
        version in a dropdown invites a second mapping that means something
        different.
        """
        best: dict[str, Transform] = {}
        for transform in self._items.values():
            current = best.get(transform.name)
            if current is None or transform.version > current.version:
                best[transform.name] = transform
        return tuple(best[name] for name in sorted(best))

    def names(self) -> tuple[str, ...]:
        return tuple(sorted({transform.name for transform in self._items.values()}))

    def describe(self) -> list[dict[str, Any]]:
        return [transform.to_dict() for transform in self.latest()]


#: The process-wide registry, preloaded with the built-ins.
REGISTRY = Registry(BUILTINS)


def registry() -> Registry:
    """The process-wide registry. A function so a caller cannot replace it."""
    return REGISTRY


def resolve(name: str, version: int | None = None) -> Transform | None:
    return REGISTRY.resolve(name, version)


def describe() -> list[dict[str, Any]]:
    return REGISTRY.describe()
