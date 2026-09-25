"""Exercise the Jev gates end to end: gating, auditing, and hot-topic choice."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.jev import AUDIT_LOG, Jev  # noqa: E402

client = Jev()

print("### 1. thin design doc should NOT pass the coding-ready gate")
thin = client.validate_design(
    "WF-001",
    "Room analytics dashboard. Shows charts of buyer activity. Data comes from the database.",
)
print(thin.summary())
print("passed:", thin.passed, "\n")

print("### 2. hot-topic: pick an approach from a solution pool")
choice = client.choose_approach(
    problem=(
        "SQLite is single-writer, but 100+ sub-agents will write concurrently from separate "
        "git worktrees against one shared database file. Writes are corrupting each other."
    ),
    options={
        "wal_and_busy_timeout": (
            "Enable WAL journal mode and set a long busy_timeout, retrying on SQLITE_BUSY. "
            "Single shared file, no architectural change."
        ),
        "per_writer_database": (
            "Give each worktree its own SQLite file and merge audit logs afterwards. "
            "No write contention, but cross-worktree reads need a merge step."
        ),
        "external_lock_service": (
            "Put a lock service in front of SQLite so only one writer runs at a time. "
            "Serialises everything, adds a process that must be operated."
        ),
    },
    context={
        "requirement": "Every SQLite change must be auditable and the UI must display the audit log.",
        "stack": "Python backend, SQLite, 100+ parallel sub-agents in separate git worktrees",
    },
)
print(choice.summary())
print()

print("### 3. audit log")
log = Path(AUDIT_LOG)
print("log path :", log)
print("entries  :", sum(1 for _ in log.open(encoding="utf-8")))
last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
print("last keys:", sorted(last))
print("last id  :", last["audit_id"], "| verdict:", last["verdict"])
