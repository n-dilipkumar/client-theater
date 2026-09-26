#!/usr/bin/env python3
"""Seed the Digital Sales Room with a realistic demo dataset.

Run against a throwaway database for local development:

    DSR_DB_PATH=data/dsr.db python backend/seed.py

Every write goes through the audited store, so running this twice produces a
second, complete set of audit rows rather than overwriting the first.

Rooms are created through :func:`dsr.rooms.create_room` rather than by hand, so
the demo data is bound to an account, pinned to a template version, and given
its site by exactly the same code path the API uses. There is no second
implementation to drift.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from dsr.db.audited import AuditedDatabase  # noqa: E402
from dsr.rooms import create_room, slugify  # noqa: E402
from dsr.store import RecordStore  # noqa: E402

# Wizard step 1 selects one of these. The membership graph a team would sync
# from a directory lives here as ordinary schema-flexible fields.
ACCOUNTS = [
    {
        "name": "Northwind Traders",
        "domain": "northwind.example",
        "tier": "enterprise",
        "member_count": 6,
        "contact_count": 4,
    },
    {
        "name": "Contoso Health",
        "domain": "contoso.example",
        "tier": "enterprise",
        "member_count": 4,
        "contact_count": 3,
    },
    {
        "name": "Fabrikam Logistics",
        "domain": "fabrikam.example",
        "tier": "mid-market",
        "member_count": 3,
        "contact_count": 2,
    },
    {
        "name": "Adventure Works",
        "domain": "adventure.example",
        "tier": "mid-market",
        "member_count": 2,
        "contact_count": 5,
    },
]

ROOMS = [
    {
        "name": "Northwind Traders — Enterprise Evaluation",
        "account": "Northwind Traders",
        "template_id": "tpl_guided_evaluation",
        "stage": "evaluation",
        "owner": "dana",
        "seats": 40,
        "branding": {"theme": "dark", "accent": "#22c55e", "logo": "northwind.svg"},
        "integrations": ["salesforce", "zoom"],
        "expires_at": "2026-12-31",
    },
    {
        "name": "Contoso Health — Security Review",
        "account": "Contoso Health",
        "template_id": "tpl_technical_review",
        "stage": "discovery",
        "owner": "sam",
        "seats": 12,
        "branding": {"theme": "light", "accent": "#0ea5e9"},
        "integrations": ["salesforce"],
        "requires_nda": True,
    },
    {
        "name": "Fabrikam Logistics — Renewal",
        "account": "Fabrikam Logistics",
        "template_id": "tpl_standard",
        "stage": "negotiation",
        "owner": "dana",
        "seats": 25,
        "branding": {"theme": "dark", "accent": "#f59e0b"},
        "integrations": ["salesforce", "slack", "hubspot"],
        "renewal_date": "2026-11-15",
    },
    {
        "name": "Adventure Works — Pilot",
        "account": "Adventure Works",
        "template_id": "tpl_standard",
        "stage": "closed",
        "owner": "sam",
        "seats": 8,
        "branding": {"theme": "dark", "accent": "#a855f7"},
        "integrations": ["teams"],
        "won_at": "2026-08-30",
    },
]

DOCUMENTS = [
    ("Enterprise Overview Deck", "deck", 24, True),
    ("Security & Compliance Pack", "pdf", 48, False),
    ("Pricing One-Pager", "pdf", 2, True),
    ("Implementation Roadmap", "pdf", 6, True),
    ("Customer Reference — Northwind", "video", 1, True),
    ("API Integration Guide", "pdf", 32, False),
    ("Contract Draft", "pdf", 18, False),
    ("Mutual Action Plan", "pdf", 4, True),
]

ACTIVITIES = [
    "viewed",
    "downloaded",
    "shared",
    "commented",
    "opened_link",
    "completed_section",
]

PEOPLE = [
    "a.buyer@northwind.example",
    "b.buyer@northwind.example",
    "procurement@contoso.example",
    "ops@fabrikam.example",
    "lead@adventure.example",
]


def main() -> int:
    rng = random.Random(20260926)  # deterministic demo data
    db_path = os.environ.get("DSR_DB_PATH", str(ROOT / "data" / "dsr.db"))
    mirror = os.environ.get("DSR_AUDIT_DIR", str(ROOT / "data" / "audit"))

    db = AuditedDatabase(db_path, mirror_dir=mirror, actor="seed")
    store = RecordStore(db)
    print(f"seeding {db_path}")

    if db.count("room") > 0:
        print("database already has rooms; pass DSR_DB_PATH pointing at a fresh file to reseed")
        db.close()
        return 1

    now = datetime.now(timezone.utc)
    room_ids: list[tuple[str, str]] = []

    accounts: dict[str, str] = {}
    for spec in ACCOUNTS:
        record = store.create("account", spec, actor="sam", source="seed")
        accounts[spec["name"]] = record["id"]
    print(f"  accounts  {len(ACCOUNTS)}")

    for spec in ROOMS:
        # `extra` carries the demo fields the workflow does not own, which is the
        # same path a team's own fields take through the API.
        room = create_room(
            store,
            name=spec["name"],
            account_id=accounts[spec["account"]],
            template_id=spec["template_id"],
            friendly_url=spec["name"],
            actor=spec["owner"],
            created_by=spec["owner"],
            extra={key: value for key, value in spec.items() if key not in ("name", "template_id")},
            request_id=f"seed-{slugify(spec['name'])}",
        )
        room_ids.append((room["id"], spec["account"]))
        print(f"  room      {spec['name']}  ->  /{room['data']['friendly_url']}")

    for index, (title, kind, pages, public) in enumerate(DOCUMENTS):
        room_id, account = room_ids[index % len(room_ids)]
        db.create(
            "document",
            {
                "title": title,
                "kind": kind,
                "pages": pages,
                "public": public,
                "views": rng.randint(4, 180),
                "avg_dwell_seconds": rng.randint(20, 400),
                "status": "published",
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
    print(f"  documents {len(DOCUMENTS)}")

    for step in range(140):
        room_id, account = rng.choice(room_ids)
        happened = now - timedelta(minutes=rng.randint(0, 60 * 24 * 21))
        db.create(
            "activity",
            {
                "person": rng.choice(PEOPLE),
                "account": account,
                "action": rng.choice(ACTIVITIES),
                "target": rng.choice([d[0] for d in DOCUMENTS]),
                "seconds_on_page": rng.randint(3, 900),
                "device": rng.choice(["desktop", "mobile", "tablet"]),
                "country": rng.choice(["AU", "US", "GB", "DE", "SG"]),
                "occurred_at": happened.isoformat(timespec="seconds"),
            },
            room_id=room_id,
            actor="system",
            source="seed",
        )
    print("  activities 140")

    stats = db.stats()
    print(f"\ndone: {stats['records']} live records, {stats['audit_entries']} audit entries")
    print(f"by action: {stats['by_action']}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
