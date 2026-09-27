"""The outbound seam: what the connector would send, and what came back.

Why this is a seam and not an HTTP client
-----------------------------------------

The researched content of WF-038 is the *request*: the chunking, the
``attributes.type`` per item, the external-id key with no record id, the
``allOrNone`` parameter, the ``idProperty`` selector, the ``Targets`` collection
with ``@odata.type`` and ``@odata.id``. It is also the *response*: a per-item
``success`` flag and ``errors`` array, a ``204 NoContent``, and results that come
back in request order.

None of that is observable from a test unless the request can be inspected
without a socket, and a socket would mean a test suite that needs credentials, a
network, and a CRM to be green - the surest way for a researched rule to go
untested. So the connector is written against :class:`Transport`, and this module
ships:

* :class:`OutboundRequest` / :class:`OutboundResponse` - the exact shape that
  goes over the wire, so the request is data a test can assert on and a
  ``/preview`` route can hand a reviewer;
* :class:`ScriptedTransport` - a deterministic stand-in that answers from a
  script, used by the suite and by the demo seeder (which must never open a
  socket);
* :class:`RecordingTransport` - records requests and replays a fixed response,
  for asserting *how many* requests a run made.

Shipping no real network client is a deliberate omission, recorded in the
repository report and in :mod:`dsr.crm_upsert.inferences`. A team that wants to
talk to a live CRM implements :meth:`Transport.send`; nothing in this package
changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

#: The API version the research writes as ``vXX.X``. Substituted into a
#: capability's path templates so a connection can move versions without a code
#: change.
DEFAULT_API_VERSION = "vXX.X"


def path_segment(value: Any) -> str:
    """Percent-encode one path segment.

    Salesforce's researched single-row form puts the key value *in the path*:
    "PATCH /services/data/vXX.X/sobjects/{sObject}/{fieldName}/{fieldValue}". So
    a value containing a slash or a space would otherwise silently become two
    path segments and address a different record - or a 404 that looks like a
    missing key rather than a mangled URL.

    This is *not* the researched workaround for the email/TLD 404 the research
    records as an unverified gap; see :func:`dsr.crm_upsert.connections.lint`.
    Encoding fixes a malformed URL. It does not fix a key field whose values
    collide, and nothing here claims it does.
    """
    from urllib.parse import quote

    return quote(str(value), safe="")


def odata_string(value: Any) -> str:
    """Quote a value for a Dataverse ``@odata.id`` alternate-key segment.

    A Dataverse alternate key is addressed as ``entityset(keyname='value')``, and
    a value containing an apostrophe would otherwise close the literal early and
    produce a different key comparison than the one asked for. Single quotes are
    doubled, which is what the OData string-literal form specifies.
    """
    return str(value).replace("'", "''")


@dataclass(frozen=True)
class OutboundRequest:
    """One request the connector would send.

    Frozen and fully explicit, including the query string, because this object is
    what the research is actually about: a reviewer should be able to read one of
    these and check it against the vendor's own documentation without reading any
    Python.
    """

    method: str
    path: str
    body: Mapping[str, Any] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    #: Why this request exists: the chunk it belongs to, and the rows in it.
    chunk_index: int = 0
    row_indexes: tuple[int, ...] = ()

    def url(self, base: str = "") -> str:
        """The full URL, query string included, for display and for a test."""
        url = f"{base}{self.path}"
        if self.query:
            pairs = "&".join(f"{key}={value}" for key, value in self.query.items())
            url = f"{url}?{pairs}"
        return url

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "url": self.url(),
            "query": dict(self.query),
            "body": json.loads(json.dumps(self.body, default=str)),
            "chunk_index": self.chunk_index,
            "row_indexes": list(self.row_indexes),
        }


@dataclass(frozen=True)
class OutboundResponse:
    """What came back.

    ``body`` stays whatever the vendor sent. Interpretation is
    :mod:`dsr.crm_upsert.runs`' job, not the transport's, so a scripted response
    can be as terse or as fussy as a real one.
    """

    status: int
    body: Any = None
    #: Set by :class:`ScriptedTransport` when the script wants to note why.
    note: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= int(self.status) < 300


@runtime_checkable
class Transport(Protocol):
    """What the connector needs from the outside world.

    One method. Everything the researched rules care about - the path, the query
    string, the body shape, the status, the per-item results - travels through
    this call, so a test can assert on all of it without a network.
    """

    def send(self, request: OutboundRequest) -> OutboundResponse:  # pragma: no cover - protocol
        ...


class ScriptedTransport:
    """A transport that answers from a script, and records what it was asked.

    The script is a callable, a single response, or a mapping keyed by a
    substring of the path. A callable is the interesting one: a test that needs
    "the first attempt creates, the second updates the same key" implements the
    researched ordering itself rather than asserting against a canned body.

    Counted per path, so a run that should make three requests and makes four is
    a failing test rather than a passing one.
    """

    def __init__(
        self,
        script: Mapping[str, OutboundResponse]
        | Callable[[OutboundRequest], OutboundResponse]
        | OutboundResponse
        | None = None,
        *,
        default: OutboundResponse | None = None,
        note: str = "scripted",
    ) -> None:
        self._script = script
        self._default = default or OutboundResponse(status=204)
        #: Recorded on any run this transport serves, so a scripted answer is
        #: never read as a real vendor's.
        self.note = note
        self.requests: list[OutboundRequest] = []
        self.calls: dict[str, int] = {}

    # -- Transport ---------------------------------------------------------- #

    def send(self, request: OutboundRequest) -> OutboundResponse:
        self.requests.append(request)
        self.calls[request.path] = self.calls.get(request.path, 0) + 1

        script = self._script
        if script is None:
            return self._default
        if callable(script):
            return script(request)
        if isinstance(script, OutboundResponse):
            return script
        for fragment, response in script.items():
            if fragment in request.path:
                return response
        return self._default

    # -- assertions helpers -------------------------------------------------- #

    @property
    def count(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[Any]:
        return [request.body for request in self.requests]

    def paths(self) -> list[str]:
        return [request.path for request in self.requests]

    def queries(self) -> list[Mapping[str, str]]:
        return [request.query for request in self.requests]


class RecordingTransport:
    """Wraps another transport and keeps every exchange.

    Useful where the interesting assertion is "the connector sent the key in the
    path and not in the body" across a whole run, rather than on one request.
    """

    def __init__(self, inner: Transport | None = None) -> None:
        self.inner: Transport = inner or ScriptedTransport()
        self.exchanges: list[tuple[OutboundRequest, OutboundResponse]] = []

    def send(self, request: OutboundRequest) -> OutboundResponse:
        response = self.inner.send(request)
        self.exchanges.append((request, response))
        return response

    @property
    def requests(self) -> list[OutboundRequest]:
        return [request for request, _response in self.exchanges]

    @property
    def responses(self) -> list[OutboundResponse]:
        return [response for _request, response in self.exchanges]


class SimulatedTransport:
    """Answers each vendor's **documented** response shape, derived from the request.

    This is the default for a deployment with no CRM credentials, and it exists
    because the alternative - a transport that answers ``204`` to everything - is
    worse than useless for a vendor whose response the research quotes. Salesforce
    documents a list of ``UpsertResult`` objects for its collections upsert, so a
    body-less 204 is an anomaly, and the connector would correctly report the whole
    chunk as unconfirmable. The page would then show every row failing for a
    reason that has nothing to do with the room.

    So this answers the shape each vendor documents, with one result per item
    *actually in the request* - never per configured row, because a mismatched
    count is a real error the connector is right to raise.

    It is a simulation and says so: :attr:`note` is ``"simulated"``, and a run
    served by it records that in the sync log, so a reviewer reading a run can
    never mistake a simulated confirmation for one a real CRM gave. It is not
    evidence about any vendor's behaviour beyond what the research quotes.
    """

    note = "simulated"

    #: The keys the simulated CRM "already holds", shared across instances.
    #:
    #: A key in here is answered ``updated``; anything else is answered
    #: ``created``, which is the researched split between "key found" and "key not
    #: found".
    #:
    #: Shared on purpose, and process-wide. One instance is built per request, so a
    #: per-instance set would be empty every time and *nothing* would ever be
    #: answered ``updated`` - a page showing only creates, which hides half of what
    #: the workflow does. Sharing it means a second run of the same key answers
    #: ``updated``, which is what a real CRM would say.
    #:
    #: The cost is that it is order-dependent, so :meth:`reset` exists and the test
    #: suite calls it. A single deployed process serves one database, so the memory
    #: matches the product it stands in for; a suite that shares a process does not.
    _known: set[str] = set()

    @classmethod
    def reset(cls) -> None:
        """Forget every key the simulated CRM has seen.

        Not something a deployment needs; it is what makes a test suite
        deterministic when several tests queue the same external ids.
        """
        cls._known.clear()

    def __init__(self, *, existing_keys: Sequence[str] = ()) -> None:
        self._known |= set(existing_keys)
        self.requests: list[OutboundRequest] = []

    @property
    def existing(self) -> set[str]:
        return self._known

    def send(self, request: OutboundRequest) -> OutboundResponse:
        self.requests.append(request)
        body = request.body or {}

        if "records" in body:  # Salesforce sObject Collections
            return salesforce_upsert_results([self._sf(item) for item in body["records"]])
        if "Targets" in body:  # Dataverse UpsertMultiple
            return dataverse_upsert_multiple()
        if "inputs" in body:  # HubSpot batch upsert
            return hubspot_upsert_results(
                [
                    {
                        "id": f"sim-{abs(hash(str(item.get('id')))) % 100000:05d}",
                        "new": str(item.get("id")) not in self.existing,
                    }
                    for item in body["inputs"]
                ]
            )
        if "properties" in body:  # a single-row PATCH
            return salesforce_single(201, "sim-00001", created=True)
        return OutboundResponse(status=204)

    def _sf(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """One ``UpsertResult``, in the shape Salesforce documents.

        The key is read from the item's *external id* field rather than assumed,
        because the researched payload puts the key in the body alongside
        ``attributes`` and a simulator that guessed the field name would
        mis-create every row.
        """
        payload = {k: v for k, v in item.items() if k != "attributes"}
        key = str(next(iter(payload.values()), ""))
        existed = key in self.existing
        self.existing.add(key)
        return {
            "id": f"sim-{abs(hash(key)) % 100000:05d}",
            "success": True,
            "created": not existed,
            "errors": [],
        }


# --------------------------------------------------------------------------- #
# Response builders for the documented shapes
# --------------------------------------------------------------------------- #


def salesforce_upsert_results(
    results: Sequence[Mapping[str, Any]],
) -> OutboundResponse:
    """A Salesforce sObject Collections upsert response.

    "This method returns a list of ``UpsertResult`` objects" and they "are
    returned in the same order" as the request body, each carrying a ``success``
    flag, a ``created`` flag, and an ``errors`` array. A 200 with this body is the
    successful case; the per-item failures ride inside it, which is exactly why
    a run must not read the HTTP status as the run's outcome.
    """
    return OutboundResponse(status=200, body=list(results))


def salesforce_error(status: int, message: str, status_code: str = "400") -> OutboundResponse:
    """A Salesforce whole-request error, e.g. the 300 for a duplicate key."""
    return OutboundResponse(
        status=status,
        body=[{"errorCode": status_code, "message": message}],
    )


def salesforce_single(
    status: int = 201, record_id: str = "001000000000001", created: bool = True
) -> OutboundResponse:
    """A Salesforce single-row upsert response.

    The researched status distinction: "``201`` - 'Created' success code, for
    POST requests and some PATCH requests" and "``204`` - 'No Content' success
    code, for DELETE requests and some PATCH requests". A single-row upsert PATCH
    is one of those some, so 201 means the key was not found and a record was
    created, and 204 means the key was found and the record was updated. The
    connector reads the status rather than guessing.
    """
    if int(status) == 204:
        return OutboundResponse(status=204)
    return OutboundResponse(
        status=status, body={"id": record_id, "success": True, "created": bool(created)}
    )


def hubspot_upsert_results(
    results: Sequence[Mapping[str, Any]], status: str = "COMPLETE"
) -> OutboundResponse:
    """A HubSpot batch-upsert response.

    The research quotes the endpoint and the ``idProperty`` selector, and the
    data flow's "per-item ``success`` flag + ``errors`` array in the response",
    but not HubSpot's own envelope. The ``results``/``new`` shape below is a
    local reading, listed in :mod:`dsr.crm_upsert.inferences`; the interpreter
    accepts a per-item ``success`` flag too, so a response in the data flow's
    shape is understood without a change here.
    """
    return OutboundResponse(status=200, body={"status": status, "results": list(results)})


def dataverse_upsert_multiple() -> OutboundResponse:
    """A Dataverse ``UpsertMultiple`` response: "returns ``204 NoContent``"."""
    return OutboundResponse(status=204, body=None, note="UpsertMultiple returns 204 NoContent")
