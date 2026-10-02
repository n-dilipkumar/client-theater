"""Regression test: the seeder must not assume its parent directory exists.

`sqlite3.connect` does not create intermediate directories. Pointing `DSR_DB_PATH`
at a path under a directory that does not exist yet died with:

    sqlite3.OperationalError: unable to open database file

which names neither the file nor the directory, so the reader has to guess. It
bites exactly the person most likely to set the variable - someone verifying the
demo against a throwaway path - and it bit this project during a routine
"reseed and check the app" step, where the seeder's error was filtered out of the
output and the empty demo dataset was nearly attributed to something else.

The test runs the real seeder as a subprocess into a nested path whose parent
does not exist, and asserts both that it exits 0 and that it actually wrote rooms.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend" / "seed.py"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
# The repo venv on Windows; fall back to whatever python is running the tests so
# this also works on CI's ubuntu runner, which has no .venv directory.
PYTHON = VENV_PY if VENV_PY.exists() else Path(sys.executable)


def test_seed_creates_missing_parent_directories(tmp_path):
    """A nested DSR_DB_PATH under a directory that does not exist must work."""
    nested_db = tmp_path / "does" / "not" / "exist" / "demo.db"
    nested_audit = tmp_path / "also" / "missing" / "audit"

    assert not nested_db.parent.exists(), "precondition: parent must not exist"

    env = dict(os.environ)
    env["DSR_DB_PATH"] = str(nested_db)
    env["DSR_AUDIT_DIR"] = str(nested_audit)

    result = subprocess.run(
        [str(PYTHON), str(SEED)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )

    assert result.returncode == 0, (
        f"seeder failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
    assert nested_db.exists(), (
        f"seeder reported success but wrote no database\n{result.stdout[-1500:]}"
    )
    assert "room " in result.stdout, (
        f"seeder wrote a database but no rooms, so the demo would be empty:\n{result.stdout[-1500:]}"
    )


def test_seed_still_refuses_to_overwrite_a_seeded_database(tmp_path):
    """The fresh-path guard must survive the mkdir change.

    Creating the parent directory is a convenience; it must not turn the seeder
    into something that silently reseeds a live demo database.
    """
    db = tmp_path / "demo.db"
    audit = tmp_path / "audit"

    def run():
        env = dict(os.environ)
        env["DSR_DB_PATH"] = str(db)
        env["DSR_AUDIT_DIR"] = str(audit)
        return subprocess.run(
            [str(PYTHON), str(SEED)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )

    first = run()
    assert first.returncode == 0, (
        f"first seed failed:\n{first.stdout[-1500:]}\n{first.stderr[-1500:]}"
    )

    second = run()
    assert second.returncode == 1, (
        "seeding twice into the same database should be refused, not silently allowed"
    )
    assert "already has rooms" in second.stdout, (
        f"expected the fresh-path guard to explain itself, got:\n{second.stdout[-800:]}"
    )
