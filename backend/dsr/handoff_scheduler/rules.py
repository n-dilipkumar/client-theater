"""The request context a routing rule reads, and the match test.

The researched payload for the init call is either
``{type:"GuestEmailRequest", guestEmail, interval}`` or
``{type:"CrmRequest", id, interval}``, "plus optional ``routerId`` and
``crmExplicits`` (extra CRM context for routing rules)". So the context a rule is
evaluated against has three researched fields and any number of integrator ones,
and the extensibility note is explicit that the integrator's fields are the point:

    "``crmExplicits`` lets an integrator pass arbitrary CRM context into routing
    rules"

**Which CRM fields are permitted is not decided by the research**, so this build
permits all of them. A fixed allow-list would be the one design that defeats the
feature: an integrator who needs a field this build did not think of would have
to come back for a release, which is exactly the coordination cost the schema-flexible
store exists to remove.

Two rules keep that from being loose rather than useful, and both are recorded as
inferences in :mod:`dsr.handoff_scheduler.inferences`:

* the three researched fields are written **after** the explicits, so an explicit
  can never shadow ``guest_email`` or ``crm_record_id`` and make a rule match the
  wrong lead. A shadowing key is dropped from the context and reported in the
  response rather than silently discarded, and the routing keeps the explicits
  verbatim so nothing is lost.
* a comparison strips surrounding whitespace and ignores case. An SDR typing a
  guest email into a web form will not reproduce the capitalisation the CRM
  record holds, and a rule that misses on capitalisation sends the lead nowhere
  with no error.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.handoff_scheduler.errors import HandoffError

#: The two researched request types, spelled exactly as the researched payload
#: spells them. They are stored in ``records.data`` because a routing keeps the
#: request it was opened for, so they must round-trip unchanged.
GUEST_EMAIL_REQUEST = "GuestEmailRequest"
CRM_REQUEST = "CrmRequest"

REQUEST_TYPES: tuple[str, ...] = (GUEST_EMAIL_REQUEST, CRM_REQUEST)

#: The context keys this build owns. An integrator's explicit may not take one of
#: these names; the researched field always wins and the collision is reported.
RESERVED_FIELDS: tuple[str, ...] = ("request_type", "guest_email", "crm_record_id")

#: The researched CrmRequest names the record field ``id``. The context calls it
#: ``crm_record_id`` so a rule cannot confuse it with an envelope id, and the
#: researched spelling is recorded on the vocabulary entry beside it.
CRM_RECORD_FIELD = "id"


def build_context(
    request_type: str,
    *,
    guest_email: str | None = None,
    crm_record_id: str | None = None,
    explicits: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The context a rule is evaluated against, and the keys an explicit lost.

    Returned as a pair rather than just the context because a dropped key is a
    fact the caller has to be able to show: an integrator whose rule suddenly
    stops matching needs to know that the field they passed was discarded and
    why, and the only place that can be answered is the response.
    """
    context: dict[str, Any] = {}
    shadowed: list[str] = []
    for key, value in (explicits or {}).items():
        name = str(key).strip()
        if not name:
            continue
        if name in RESERVED_FIELDS:
            shadowed.append(name)
            continue
        context[name] = value

    # Written last, so an explicit can never shadow a researched field.
    context["request_type"] = request_type
    if guest_email:
        context["guest_email"] = guest_email
    if crm_record_id:
        context["crm_record_id"] = crm_record_id
    return context, sorted(set(shadowed))


def read_request(payload: Mapping[str, Any]) -> tuple[str, str | None, str | None, dict[str, Any]]:
    """Read the researched request out of a body.

    Returns ``(request_type, guest_email, crm_record_id, crm_explicits)``. Exactly
    one of the two identities must be present, because the research gives two
    mutually exclusive payload shapes and a body carrying both is ambiguous: the
    routing would have to guess which lead it was opened for.

    ``type`` is read from ``type`` first and then from ``request_type``, because
    the researched payload spells it ``type`` and a body assembled from a
    spreadsheet column spells it the other way.
    """
    request_type = str(payload.get("type") or payload.get("request_type") or "").strip()
    if request_type not in REQUEST_TYPES:
        raise HandoffError(
            f"type must be one of {', '.join(REQUEST_TYPES)}; got {payload.get('type')!r}"
        )

    guest_email = str(payload.get("guestEmail") or payload.get("guest_email") or "").strip()
    record_id = str(payload.get("id") or payload.get("crm_record_id") or "").strip()

    if request_type == GUEST_EMAIL_REQUEST:
        if not guest_email:
            raise HandoffError(
                "guestEmail is required when type is GuestEmailRequest; which lead is this?"
            )
        if record_id:
            raise HandoffError(
                "this request carries both a guestEmail and a CRM record id. The researched "
                "payload has one shape for each: send a GuestEmailRequest or a CrmRequest"
            )
        record_id = ""
    else:
        if not record_id:
            raise HandoffError(f"id is required when type is {CRM_REQUEST}; which CRM record?")
        if guest_email:
            raise HandoffError(
                "this request carries both a CRM record id and a guestEmail. The researched "
                "payload has one shape for each: send a GuestEmailRequest or a CrmRequest"
            )
        guest_email = ""

    explicits = payload.get("crmExplicits") or payload.get("crm_explicits") or {}
    if not isinstance(explicits, Mapping):
        raise HandoffError("crmExplicits must be an object; it is a bag of CRM context, not a list")
    return request_type, guest_email or None, record_id or None, dict(explicits)


def _text(value: Any) -> str:
    """The comparison form of a context or expected value."""
    return str(value).strip().lower()


def value_matches(actual: Any, expected: Any) -> bool:
    """Whether one context value satisfies one declared expectation.

    A list of expectations is an "any of", so a path may declare
    ``{"region": ["emea", "apac"]}`` without a second path. ``None`` as the
    expectation asks for the key to be absent, which is how a path says "only a
    lead that has no product line yet".
    """
    if isinstance(expected, (list, tuple, set)):
        return any(value_matches(actual, one) for one in expected)
    if expected is None:
        return actual is None
    return _text(actual) == _text(expected)


def matches(match: Mapping[str, Any] | None, context: Mapping[str, Any]) -> bool:
    """Whether a path's ``match`` block is satisfied by the request context.

    Every declared key must be satisfied. An absent ``match`` block matches
    everything, which is how an admin writes the catch-all path that takes any
    lead the specific paths did not claim.
    """
    for key, expected in (match or {}).items():
        name = str(key).strip()
        if name not in context:
            return False
        if not value_matches(context[name], expected):
            return False
    return True


def explain_miss(match: Mapping[str, Any] | None, context: Mapping[str, Any]) -> str:
    """Why one path did not match, in a sentence an SDR can act on.

    Names the first field that failed and both values, because "no path matched"
    with nothing else tells an SDR to go and change the router rather than to
    fix the field they typed.
    """
    for key, expected in (match or {}).items():
        name = str(key).strip()
        if name not in context:
            return (
                f"the request carries no {name}. This path needs "
                f"{name}={' or '.join(str(one) for one in _as_list(expected))}"
            )
        if not value_matches(context[name], expected):
            return (
                f"the request has {name}={_text(context[name])}, and this path needs "
                f"{name}={' or '.join(str(one) for one in _as_list(expected))}"
            )
    return "this path has no match rule, so it matches every request"


def _as_list(expected: Any) -> list[Any]:
    """A declared expectation as a list, so a message can read either shape."""
    if isinstance(expected, (list, tuple, set)):
        return list(expected)
    return [expected]


def match_report(
    paths: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Each declared path with whether it matched and, when it did not, why.

    The losers are reported rather than dropped. "One or more routing paths"
    promises a list, and an SDR who typed the wrong region needs to see which
    paths their request was measured against, not only that none of them matched.
    """
    report: list[dict[str, Any]] = []
    for path in paths:
        block = path.get("match") or {}
        hit = matches(block, context)
        report.append(
            {
                "path_id": str(path.get("path_id") or ""),
                "name": str(path.get("name") or ""),
                "matched": hit,
                "match": dict(block),
                "reason": "" if hit else explain_miss(block, context),
            }
        )
    return report
