"""Collection names and the read/write helpers over them.

Every read and write in this package goes through :class:`~dsr.store.RecordStore`,
so the audit row is written in the same transaction as the change. Nothing here
opens a connection, and nothing here takes a ``source``: the ``source`` is
required on every writing call and has to be built from ``router.prefix`` at the
HTTP layer, because a domain function that hard-codes its own path is exactly the
defect the build brief names - an audit log that keeps recording a route the app
has stopped serving.

The books are thin on purpose. They know the collection and how to summarise a
row; they know nothing about the researched rules. Those live in
:mod:`dsr.intent_stream.segments`, :mod:`dsr.intent_stream.payloads` and
:mod:`dsr.intent_stream.stream`, which is what keeps a rule arguable without
reading a storage helper.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from dsr.intent_stream.vocabulary import (
    CONTACT_COLLECTION,
    DELIVERY_COLLECTION,
    LEAD_COLLECTION,
    SEGMENT_COLLECTION,
    STATE_COLLECTION,
    VISIT_COLLECTION,
    WORKFLOW_COLLECTION,
)
from dsr.store import RecordStore


class Book:
    """One collection, with the reads and writes this workflow needs."""

    def __init__(self, store: RecordStore, collection: str) -> None:
        self.store = store
        self.collection = collection

    # -- reads ------------------------------------------------------------- #

    def get(self, record_id: Any) -> dict[str, Any] | None:
        record = self.store.get(str(record_id))
        if record is None or record["collection"] != self.collection:
            return None
        return record

    def list(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.list(self.collection, **kwargs)

    def find(self, where: Mapping[str, Any], *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.find(self.collection, dict(where), limit=limit)

    # -- writes ------------------------------------------------------------ #

    def create(self, data: Mapping[str, Any], *, source: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.create(self.collection, data, source=source, **kwargs)

    def update(
        self, record_id: str, patch: Mapping[str, Any], *, source: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self.store.update(record_id, patch, source=source, **kwargs)

    def delete(self, record_id: str, *, source: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.delete(record_id, source=source, **kwargs)


def lead_state_id(workflow_id: str, lead_id: str) -> str:
    """The deterministic id for one (workflow, company lead) pair.

    Deterministic so the "only send a lead once" decision is a single lookup and
    cannot be raced into two rows by two visits at once. Two features in this
    repository already derive ids this way; see the ``lead-state-is-one-row-per
    -pair`` entry in :mod:`dsr.intent_stream.inferences`.
    """
    return f"{STATE_COLLECTION}:{workflow_id}:{lead_id}"


def summarise_workflow(record: Mapping[str, Any]) -> dict[str, Any]:
    """A workflow as a list shows it.

    Never the token: a secret readable out of a list response is an accident
    waiting to happen, and the token is on the create response and behind the
    reveal route. See :func:`dsr.intent_stream.tokens.mask`.
    """
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "name": data.get("name"),
        "type": data.get("type"),
        "url": data.get("url"),
        "active": bool(data.get("active", True)),
        "sendMode": data.get("sendMode"),
        "payload": data.get("payload"),
        "conditions": data.get("conditions"),
        "contactFilter": data.get("contactFilter"),
        "segmentCount": len((data.get("conditions") or {}).get("segmentIds") or []),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "revision": record.get("revision"),
    }


def summarise_segment(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "name": data.get("name"),
        "description": data.get("description"),
        "match": data.get("match"),
        "rules": data.get("rules") or [],
        "ruleCount": len(data.get("rules") or []),
        "summary": data.get("summary"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def summarise_lead(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "companyId": data.get("companyId"),
        "name": data.get("name"),
        "domain": data.get("domain"),
        "industry": data.get("industry"),
        "country": data.get("country"),
        "employees": data.get("employees"),
        "visitCount": data.get("visitCount", 0),
        "pagesViewed": data.get("pagesViewed") or [],
        "lastVisitAt": data.get("lastVisitAt"),
        "identifiedAt": data.get("identifiedAt"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def summarise_contact(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "leadId": data.get("leadId"),
        "name": data.get("name"),
        "title": data.get("title"),
        "department": data.get("department"),
        "email": data.get("email"),
        "seniority": data.get("seniority"),
    }


def summarise_visit(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "leadId": data.get("leadId"),
        "at": data.get("at"),
        "pagesViewed": data.get("pagesViewed") or [],
        "secondsOnPage": data.get("secondsOnPage"),
        "country": data.get("country"),
        "device": data.get("device"),
        "matchedWorkflows": data.get("matchedWorkflows", 0),
        "created_at": record.get("created_at"),
    }


def summarise_delivery(record: Mapping[str, Any]) -> dict[str, Any]:
    data = record.get("data") or {}
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "workflowId": data.get("workflowId"),
        # Carried on the row rather than joined at read time, so a delivery stays
        # readable after its workflow is deleted - which is the case the soft
        # delete on the workflow route exists for.
        "workflowName": data.get("workflowName"),
        "leadId": data.get("leadId"),
        "visitId": data.get("visitId"),
        "state": data.get("state"),
        "skipReason": data.get("skipReason"),
        "status": data.get("status"),
        "retryable": data.get("retryable"),
        "isUpdate": data.get("isUpdate"),
        "updateCount": data.get("updateCount"),
        "contactsIncluded": data.get("contactsIncluded"),
        "contactsConsidered": data.get("contactsConsidered"),
        "attempts": len(data.get("attemptLog") or []),
        "durationMs": data.get("durationMs"),
        "at": data.get("at"),
        "created_at": record.get("created_at"),
    }


class Books:
    """The seven books this workflow reads and writes."""

    def __init__(self, store: RecordStore) -> None:
        self.segments = Book(store, SEGMENT_COLLECTION)
        self.leads = Book(store, LEAD_COLLECTION)
        self.contacts = Book(store, CONTACT_COLLECTION)
        self.visits = Book(store, VISIT_COLLECTION)
        self.workflows = Book(store, WORKFLOW_COLLECTION)
        self.deliveries = Book(store, DELIVERY_COLLECTION)
        self.states = Book(store, STATE_COLLECTION)

    def all_collections(self) -> Iterable[str]:
        return (
            self.segments.collection,
            self.leads.collection,
            self.contacts.collection,
            self.visits.collection,
            self.workflows.collection,
            self.deliveries.collection,
            self.states.collection,
        )
