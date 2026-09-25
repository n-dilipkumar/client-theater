#!/usr/bin/env python3
"""Seed the Digital Sales Room with a realistic demo dataset.

Run against a throwaway database for local development:

    DSR_DB_PATH=data/dsr.db python backend/seed.py

Every write goes through the audited store, so running this twice produces a
second, complete set of audit rows rather than overwriting the first.
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

ROOMS = [
    {
        "name": "Northwind Traders — Enterprise Evaluation",
        "account": "Northwind Traders",
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
    print(f"seeding {db_path}")

    if db.count("room") > 0:
        print("database already has rooms; pass DSR_DB_PATH pointing at a fresh file to reseed")
        db.close()
        return 1

    now = datetime.now(timezone.utc)
    room_ids: list[tuple[str, str]] = []

    for spec in ROOMS:
        room = db.create("room", spec, actor=spec["owner"], source="seed")
        room_ids.append((room["id"], spec["account"]))
        print(f"  room      {spec['name']}")

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
