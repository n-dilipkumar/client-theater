"""WF-094: publish and share the quote as a hosted link or an email.

The rules live in :mod:`dsr.quote_publishing`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the vocabulary the page renders, served from one place so the panel cannot
  drift from the rules that validate it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Every write passes a ``source`` built from ``router.prefix`` and the route's own
path, rather than a label written once. That is what keeps every audit row
naming a URL the app actually serves.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-094.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.quote_publishing.publishing import QuotePublishingService, view_quote
from dsr.quote_publishing.rules import QuoteRuleError
from dsr.quote_publishing.vocabulary import (
    ACTIVITY_LINK_COPIED,
    ACTIVITY_PDF_REQUESTED,
    ACTIVITY_PUBLISHED,
    ACTIVITY_SENT,
    ACTIVITY_SHARED,
    CC_LIMIT,
    DYNAMICS_PERFORMANCE_TARGET_BYTES,
    EMAIL_ATTACHMENT_CAP_BYTES,
    KNOWN_STATUSES,
    LANGUAGES,
    LOCALES,
    PUBLISHABLE_FROM,
    TIMEZONES,
    UNLOCK_TARGETS,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-094-publish-quote",
    "ticket": "WF-094",
    "name": "Publish and share a quote",
    "description": (
        "Publish a quote to a public URL, freeze its total, and share it by link, "
        "PDF or email. The link is computed on publish and never supplied by a caller."
    ),
    "nav": [{"id": "wf-094-publish-quote", "label": "Publish a quote"}],
}

router = APIRouter(prefix="/api/WF-094", tags=["WF-094"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _quote_rule_error(request: Request, exc: QuoteRuleError) -> JSONResponse:
    """A rule refused the request.

    422 rather than 400: the request was well-formed and the rules are what
    refused it. The core ``AuditError`` handler decides its status by searching
    the message for the words "conflict" or "exists", which would make a status
    code a function of prose. This type is the workflow's own, so the mapping is
    declared here and does not depend on how a message is worded.
    """
    return JSONResponse(
        status_code=422, content={"error": "quote_rule_refused", "detail": str(exc)}
    )


EXCEPTION_HANDLERS = {QuoteRuleError: _quote_rule_error}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_service(store: RecordStore = StoreDep) -> QuotePublishingService:
    """The service, built on the shared audited store.

    Composed from the documented dependency seam rather than reaching for
    ``request.app.state``, so a test can construct it and the audit row is still
    written in the same transaction as the change.
    """
    return QuotePublishingService(store)


ServiceDep = Depends(get_service)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""
    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every name, ceiling and cap the rules use, served from one place.

    The page renders these rather than repeating them, so a panel cannot tell a
    seller the email cap is 25 MB when the rule refuses at 20.
    """
    return {
        "ticket": "WF-094",
        "statuses": list(KNOWN_STATUSES),
        "publishable_from": list(PUBLISHABLE_FROM),
        "unlock_targets": list(UNLOCK_TARGETS),
        "activities": [
            ACTIVITY_PUBLISHED,
            ACTIVITY_SENT,
            ACTIVITY_SHARED,
            ACTIVITY_LINK_COPIED,
            ACTIVITY_PDF_REQUESTED,
        ],
        "languages": list(LANGUAGES),
        "locales": list(LOCALES),
        "timezones": list(TIMEZONES),
        "limits": {
            "cc": CC_LIMIT,
            "email_attachment_cap_bytes": EMAIL_ATTACHMENT_CAP_BYTES,
            # Recorded so it is never mistaken for a second cap. It is a Dynamics
            # generation-performance note and no rule here enforces it.
            "dynamics_performance_target_bytes": DYNAMICS_PERFORMANCE_TARGET_BYTES,
        },
    }


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    store: RecordStore = StoreDep,
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Counts a seller needs before opening a quote."""
    quotes = service.list_quotes(room_id=room_id, limit=1000)
    by_status = {status: 0 for status in KNOWN_STATUSES}
    for quote in quotes:
        status = str(quote["data"].get("status") or "").upper()
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "quotes": len(quotes),
        "by_status": by_status,
        "published": sum(1 for q in quotes if q["data"].get("hs_locked") is True),
        "shareable": sum(1 for q in quotes if q["data"].get("hs_quote_link")),
        "sent": sum(1 for q in quotes if q["data"].get("sent") is True),
        "settings": service.settings(room_id=room_id),
    }


@router.get("/settings")
def settings(
    room_id: str | None = Query(default=None),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """The quote domain and locale settings, with the fallback marked."""
    return service.settings(room_id=room_id)


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Quotes, each with its derived publish facts."""
    records = service.list_quotes(room_id=room_id, status=status, limit=limit)
    return {"count": len(records), "entries": [view_quote(r) for r in records]}


@router.get("/quotes/{quote_id}")
def get_quote(quote_id: str, service: QuotePublishingService = ServiceDep) -> dict[str, Any]:
    """One quote with the public link and the frozen total when it has them."""
    return view_quote(service.require_quote(quote_id))


@router.post("/quotes/{quote_id}/publish")
def publish(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Publish a quote and freeze its total, or share it without freezing.

    The body may carry ``shared_only``. It may **not** carry a link, a slug or a
    domain: the research calls ``hs_quote_link`` read-only and says
    ``hs_domain`` and ``hs_slug`` are set by state. A body that supplies one is
    refused rather than silently ignored, because a caller who sets a link and
    watches it be discarded has been told the publish worked.
    """
    for key in ("hs_quote_link", "hs_slug", "hs_domain", "public_link"):
        if payload.get(key):
            raise QuoteRuleError(
                f"{key} is computed on publish and cannot be set by a caller; "
                "publish the quote and read the link back"
            )
    quote = service.publish(
        quote_id,
        actor=payload.get("actor"),
        shared_only=bool(payload.get("shared_only")),
        source=_source("POST", f"/quotes/{quote_id}/publish"),
    )
    return quote


@router.post("/quotes/{quote_id}/unlock")
def unlock(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Release a published quote's totals by moving it to an editable status."""
    target = payload.get("target") or payload.get("status")
    if not target:
        raise QuoteRuleError("an unlock needs a target status: one of " + ", ".join(UNLOCK_TARGETS))
    return service.unlock(
        quote_id,
        target,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/unlock"),
    )


@router.post("/quotes/{quote_id}/link")
def copy_link(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """The link a seller would copy, recorded as an activity."""
    return service.share_link(
        quote_id,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/link"),
    )


@router.post("/quotes/{quote_id}/emails")
def send_email(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Record a quote email and its share event.

    ``pdf_size_bytes`` is accepted so the cap can be applied: the email is sent
    either way, and an oversize PDF is dropped from it.
    """
    size = payload.get("pdf_size_bytes")
    return service.send_email(
        quote_id,
        to=payload.get("to"),
        cc=payload.get("cc"),
        contact_email=payload.get("contact_email"),
        subject=payload.get("subject"),
        body=payload.get("body"),
        from_address=payload.get("from"),
        pdf_size_bytes=int(size) if size is not None else None,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/emails"),
    )


@router.post("/quotes/{quote_id}/pdf")
def request_pdf(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """Record a PDF download request and name its output location."""
    return service.request_download(
        quote_id,
        file_name=payload.get("file_name"),
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/pdf"),
    )


@router.get("/quotes/{quote_id}/activity")
def quote_activity(
    quote_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    service: QuotePublishingService = ServiceDep,
) -> dict[str, Any]:
    """A quote's activity stream, newest first."""
    records = service.list_activity(quote_id, limit=limit)
    return {"count": len(records), "entries": records}


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Four quotes covering every state this workflow distinguishes.

    A draft that has never been published, a published quote whose total is
    frozen, a shared quote whose total is still editable, and one that has been
    emailed so its activity stream is not empty. Without the published and sent
    rows the page a reviewer opens would show nothing to publish or nothing to
    share, and a feature nobody can review is a feature that has not shipped.

    Every character of the returned string is cp1252-encodable. The seeder
    prints it on a Windows console, and one arrow character in one recovered
    feature broke the whole seed.
    """
    rooms = context.get("room_ids") or []
    if not rooms:
        return None
    room_id = rooms[0][0]
    now = context["now"]

    def quote(**payload):
        db.create(
            "quote",
            {**payload, "created_at": now.isoformat()},
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    quote(
        title="Northwind renewal",
        status="DRAFT",
        quote_number="Q-2026-014",
        line_items=[{"quantity": 2, "price": 1200}, {"quantity": 1, "price": 450}],
    )
    quote(
        title="Contoso onboarding",
        status="PUBLISHED",
        quote_number="Q-2026-015",
        line_items=[{"quantity": 1, "price": 8400}],
        hs_locked=True,
        hs_quote_amount=8400.0,
        locked_amount=8400.0,
        hs_domain="quotes.website.com",
        hs_slug="q-2026-015",
        hs_quote_link="https://quotes.website.com/q-2026-015",
        published_at=now.isoformat(),
    )
    quote(
        title="Fabrikam expansion",
        status="SHARED",
        quote_number="Q-2026-016",
        line_items=[{"quantity": 3, "price": 2200}],
        hs_locked=False,
        hs_domain="billing.northwind.example",
        hs_slug="q-2026-016",
        hs_quote_link="https://billing.northwind.example/q-2026-016",
        shared_at=now.isoformat(),
    )
    quote(
        title="Adventure Works pilot",
        status="PUBLISHED",
        quote_number="Q-2026-017",
        line_items=[{"quantity": 1, "price": 15000}],
        hs_locked=True,
        hs_quote_amount=15000.0,
        locked_amount=15000.0,
        hs_domain="quotes.website.com",
        hs_slug="q-2026-017",
        hs_quote_link="https://quotes.website.com/q-2026-017",
        sent=True,
        sent_at=now.isoformat(),
        sent_to="buyer@example.com",
        published_at=now.isoformat(),
    )
    db.create(
        "quote_settings",
        {
            "domain": "billing.northwind.example",
            "language": "en",
            "locale": "en-GB",
            "timezone": "Europe/Amsterdam",
            "default_from_address": "dana@northwind.example",
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    return "4 quotes (1 draft, 2 published, 1 shared) and 1 domain setting"
