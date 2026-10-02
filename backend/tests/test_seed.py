"""Regression tests: the seeder must create its parents and refuse a reseed.

`sqlite3.connect` does not create intermediate directories. Pointing `DSR_DB_PATH`
at a path under a directory that does not exist yet died with:

    sqlite3.OperationalError: unable to open database file

which names neither the file nor the directory, so the reader has to guess. It
bites exactly the person most likely to set the variable - someone verifying the
demo against a throwaway path - and it bit this project during a routine
"reseed and check the app" step, where the seeder's error was filtered out of
the output and the empty demo dataset was nearly attributed to something else.

The second test is the other half of the same change. Creating the parent
directory is a convenience, and a convenience must not turn the seeder into
something that silently overwrites a live demo database.

Both tests drive the real seeder as a subprocess, because that is the only way to
observe what a person running `python backend/seed.py` would observe. A full seed
writes several hundred audited records and costs seconds, so the cost of these two
tests is the cost of the seeder itself rather than the cost of what is asserted.
One seed can serve both: see the `seeded` fixture for why.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "backend" / "seed.py"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
# The repo venv on Windows; fall back to whatever python is running the tests so
# this also works on CI's ubuntu runner, which has no .venv directory.
PYTHON = VENV_PY if VENV_PY.exists() else Path(sys.executable)


def run_seeder(db_path: Path, audit_dir: Path) -> subprocess.CompletedProcess:
    """Run ``backend/seed.py`` as a subprocess against the two given paths."""
    env = dict(os.environ)
    env["DSR_DB_PATH"] = str(db_path)
    env["DSR_AUDIT_DIR"] = str(audit_dir)
    return subprocess.run(
        [str(PYTHON), str(SEED)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Seed the demo database once for the whole module, and check that it worked.

    Running the seeder was what made this file the most expensive in the suite:
    one full seed costs seconds, and the file used to pay for two of them, which
    is 39 seconds of the suite's 319.

    The two tests need different *answers* from the seeder, not different data.
    The first needs a fresh nested path to succeed. The second needs a path that
    already holds rooms to be refused. Those are two outcomes of one run, so the
    run happens here, once, and the refusal test starts from the file this
    leaves behind. It is a module fixture rather than shared module state, so
    either test still passes when it is selected on its own.

    Everything asserted here is the fresh-path case, which is what the first test
    is about. Nothing is asserted twice: the second test keeps only the refusal.
    """
    tmp = tmp_path_factory.mktemp("seed")
    nested_db = tmp / "does" / "not" / "exist" / "demo.db"
    nested_audit = tmp / "also" / "missing" / "audit"

    parent_existed = nested_db.parent.exists()
    result = run_seeder(nested_db, nested_audit)

    assert not parent_existed, "precondition: the parent directory must not exist"
    assert result.returncode == 0, (
        f"seeder failed with exit {result.returncode}\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
    assert nested_db.exists(), (
        f"seeder reported success but wrote no database\n{result.stdout[-1500:]}"
    )
    assert "room " in result.stdout, (
        f"seeder wrote a database but no rooms, so the demo would be empty:\n"
        f"{result.stdout[-1500:]}"
    )
    return nested_db


def test_seed_creates_missing_parent_directories(seeded):
    """A nested DSR_DB_PATH under a directory that does not exist must work.

    The fixture ran the seeder against a path three directories deep and asserted
    the parent was absent beforehand, the process exited 0, the file appeared, and
    it reported rooms. What is left to state is the part the mkdir call is
    responsible for: every missing level now exists, and the database sits at the
    bottom of them.
    """
    assert seeded.parent.is_dir(), "the seeder must create the directory it needs"
    assert seeded.parent.parent.is_dir(), "the seeder must create every missing level"
    assert seeded.parent.parent.parent.is_dir(), "the seeder must create every missing level"
    assert seeded.is_file()


def test_seed_still_refuses_to_overwrite_a_seeded_database(seeded, tmp_path):
    """The fresh-path guard must survive the mkdir change.

    Creating the parent directory is a convenience; it must not turn the seeder
    into something that silently reseeds a live demo database.

    The starting point is a copy of the database the fixture seeded. Copying the
    file gives this test a database that already holds rooms without paying for a
    second seed, and it is the same situation the guard exists for: a path that
    has been seeded once and is being pointed at again.
    """
    already_seeded = tmp_path / "demo.db"
    shutil.copyfile(seeded, already_seeded)

    result = run_seeder(already_seeded, tmp_path / "audit")

    assert result.returncode == 1, (
        "seeding twice into the same database should be refused, not silently allowed"
    )
    assert "already has rooms" in result.stdout, (
        f"expected the fresh-path guard to explain itself, got:\n{result.stdout[-800:]}"
    )
