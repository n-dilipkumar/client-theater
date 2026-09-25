"""Resolve the foundational storage/concurrency decision with Jev, twice.

First attempt returned `uncertain` because the option set was under-differentiated.
This reframes the problem with the missing evidence: sub-agents write code and
tests, not the shared database, so the real concurrency surface is much smaller.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.jev import Jev  # noqa: E402

client = Jev()

result = client.choose_approach(
    problem=(
        "Choose the write-concurrency strategy for SQLite in a Digital Sales Room where a "
        "Python backend serves the UI and must log every write to an audit table."
    ),
    options={
        "single_writer_wal": (
            "One long-lived backend process is the ONLY writer to the shared SQLite file, in WAL "
            "mode with a generous busy_timeout. Sub-agents never write the shared database: they "
            "write source code and pytest tests that run against a throwaway temp database per test. "
            "The audit log is written by the backend in the same transaction as the data change, so "
            "audit rows can never be lost or reordered relative to the change they describe."
        ),
        "shared_db_direct_writes": (
            "Sub-agents and tests may write the shared SQLite file directly, in WAL mode with "
            "busy_timeout and retry-on-busy. Maximally convenient for ad-hoc inspection, but audit "
            "rows can be written by a process other than the one that changed the data, and an "
            "abandoned test can leave the shared file locked or dirty."
        ),
        "per_writer_databases": (
            "Each git worktree gets its own SQLite file and audit logs are merged afterwards. "
            "Zero cross-process contention, but merging is lossy on ordering, audit ids can collide "
            "across worktrees, and the running UI cannot show a coherent audit trail."
        ),
    },
    context={
        "requirement_1": "Every SQLite change must be recorded in an audit table the UI displays.",
        "requirement_2": "100+ sub-agents work in isolated git worktrees, then their branches merge.",
        "requirement_3": "The app must be runnable and verifiable on localhost.",
        "conclusion_already_established": (
            "Sub-agents write CODE, not rows in the shared database. Their tests use per-test "
            "temporary databases. Therefore the only production writer is the backend process, and "
            "the real concurrency question is just multiple backend workers plus test runs."
        ),
    },
)
print(result.summary())
print()
print("verdict:", result.verdict, "| selected:", result.selected)
