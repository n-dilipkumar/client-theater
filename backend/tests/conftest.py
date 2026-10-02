"""Shared fixtures for the backend test suite.

There was no ``conftest.py`` in this directory before this file. Each of the 75
test modules built its own fixtures, and 58 of them built a ``TestClient``
inside the test function. That is the single most expensive thing the suite
does, and this module is the one place where it can be fixed once instead of 58
times.

Why entering a ``TestClient`` is the expensive part
---------------------------------------------------
``with TestClient(app)`` runs the application lifespan. The lifespan in
``dsr/api.py`` does exactly two things::

    db = AuditedDatabase(path, mirror_dir=..., actor="api")
    app.state.db = db
    app.state.store = RecordStore(db)

Opening the database is the half a test genuinely needs, because a test must
never see another test's rows. Running the lifespan is the half it does not: it
re-runs a function whose entire body is "open a database and put it on
``app.state``". **A test needs a fresh database, not a fresh application.**

``dsr/deps.py`` resolves the store on every single request, through
``request.app.state.store``, and it reads the database path from the
environment at call time rather than at import time. Both of those are what
make the cheap option available: reassigning ``app.state`` between tests is
enough, and the routes do not have to know it happened.

So the fixtures below enter **one** ``TestClient`` per module and swap
``app.state`` per test. ``module_client`` owns the expensive part. ``client``
owns the isolation.

Measured cost per test, 25 iterations each, on the development machine:

===========================================  =========
Strategy                                      ms/test
===========================================  =========
A  file database + full lifespan per test      15.55
B  in-memory database + full lifespan          4.14
C  one TestClient per module + app.state swap  1.25
===========================================  =========

Strategy C is what ``client`` implements.

What a test file has to change to use this
------------------------------------------
Nothing. Every fixture here is named after the name this suite already uses,
so adopting one is a matter of deleting the local copy:

* ``client`` replaces a local ``client(monkeypatch)`` or ``http(monkeypatch)``.
* ``db`` replaces a local ``db(tmp_path)``.
* ``store`` replaces a local ``store(tmp_path)`` or ``store(db)``.
* ``memory_db`` is the in-memory variant, for a test that does not need a file.

A test module that keeps its own fixture of the same name keeps it. A fixture
defined in a module always wins over one defined here, so adding this file
cannot change the behaviour of a module that has not been converted yet.

Which tests must stay on a real file
------------------------------------
A file-backed database and ``":memory:"`` are not the same thing, and three
differences are load-bearing:

1. ``":memory:"`` cannot use WAL, so the pragma is skipped. A test that reads
   ``PRAGMA journal_mode`` needs a file.
2. The JSONL audit mirror is written to ``mirror_dir``, which is a directory
   either way. A test that opens the mirror file needs ``mirror_dir`` set, not
   a file-backed database. ``db`` sets both.
3. A test that reopens the same path in a second ``AuditedDatabase`` and reads
   back the first one's rows needs the data to have outlived the connection.
   ``":memory:"`` cannot do that at all.

Use ``memory_db`` only when none of the three applies. ``store`` is backed by
``memory_db``, so a test that needs the mirror must ask for ``db`` and build
its own ``RecordStore``, which is what the existing modules already do.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The environment variables ``dsr/deps.py`` reads. Both are read at call time,
#: never at import time, which is what lets a fixture repoint them per test.
DB_PATH_VAR = "DSR_DB_PATH"
AUDIT_DIR_VAR = "DSR_AUDIT_DIR"


# --------------------------------------------------------------------------- #
# Isolation guards
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _never_touch_the_real_database(tmp_path: Path) -> Iterator[None]:
    """Keep a test that forgets to set the database path out of ``data/``.

    ``dsr/deps.py`` falls back to ``<repo>/data/dsr.db`` when ``DSR_DB_PATH`` is
    unset. That default is right for the application and wrong for a test: a
    fixture that forgets to set the path does not fail, it quietly reads and
    writes the developer's real database, and the suite passes while proving
    nothing. Point both variables at this test's own temporary directory, but
    only when the test has not already chosen for itself, so a module that
    wants a specific path still gets it.

    Every module in this suite sets both variables, so this guard changes no
    existing test. It exists so that the next module does not have to.
    """
    chosen = {name: os.environ.get(name) for name in (DB_PATH_VAR, AUDIT_DIR_VAR)}
    if chosen[DB_PATH_VAR] is None:
        os.environ[DB_PATH_VAR] = str(tmp_path / "default.db")
    if chosen[AUDIT_DIR_VAR] is None:
        os.environ[AUDIT_DIR_VAR] = str(tmp_path / "default-audit")
    try:
        yield
    finally:
        for name, value in chosen.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True)
def _no_dependency_overrides_between_tests() -> Iterator[None]:
    """Empty ``app.dependency_overrides`` before every test.

    ``dependency_overrides`` is the documented seam for replacing a service in a
    test, and it is process-wide, so an override a test installs and does not
    remove is still installed when the next test starts. Several modules in
    this suite clear the dict in their own teardown and several do not, and the
    ones that do not pass locally because they inherit the override left by
    whatever ran before them.

    Clearing before each test is the direction that cannot cause a false pass:
    it can only remove state, never add any. A test that genuinely needs an
    override installs it in its own fixture, which runs after this one.
    """
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Database fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A database file path for one test, inside that test's own directory."""
    return tmp_path / "test.db"


@pytest.fixture
def db(db_path: Path) -> Iterator[AuditedDatabase]:
    """A fresh, EMPTY, file-backed database with an on-disk audit mirror.

    File-backed on purpose. See the module docstring: WAL, the mirror file, and
    reopening the same path in a second connection are three things ``":memory:"``
    cannot do, and a test that needs any of them must not be handed this.
    """
    database = AuditedDatabase(db_path, mirror_dir=db_path.parent / "audit", actor="test")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def memory_db() -> Iterator[AuditedDatabase]:
    """A fresh, EMPTY, in-memory database, with no file and no mirror.

    Roughly seventeen times cheaper to build than the file-backed ``db``. It is
    the right default for a test that only reads and writes records.
    """
    database = AuditedDatabase(":memory:", actor="test")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def store(memory_db: AuditedDatabase) -> RecordStore:
    """A ``RecordStore`` over a fresh, EMPTY database.

    No mirror, because ``memory_db`` has none. A test that asserts on the JSONL
    mirror asks for ``db`` instead and wraps it: ``RecordStore(db)``.
    """
    return RecordStore(memory_db)


# --------------------------------------------------------------------------- #
# HTTP fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def module_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """One ``TestClient`` for the whole module, with the lifespan run once.

    Entering a ``TestClient`` runs the lifespan, and that is the cost this whole
    exercise is about, so it happens once per module rather than once per test.
    Module scope rather than session scope: the lifespan still opens a database,
    so a session-scoped client would share one open connection across every
    module in the run, and a module that closed it, replaced it, or left an
    override behind would take the rest of the suite with it. Module scope keeps
    that blast radius to one file, and costs one lifespan per file.
    """
    root = tmp_path_factory.mktemp("module-client")
    # The lifespan opens whatever DSR_DB_PATH points at, so point it at
    # something disposable before entering. Without this the first module to ask
    # for this fixture would create <repo>/data/dsr.db.
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv(DB_PATH_VAR, str(root / "lifespan.db"))
        environment.setenv(AUDIT_DIR_VAR, str(root / "lifespan-audit"))
        with TestClient(app) as test_client:
            yield test_client


@pytest.fixture
def client(module_client: TestClient, db: AuditedDatabase) -> Iterator[TestClient]:
    """A ``TestClient`` whose application state points at a fresh, EMPTY database.

    This is the fixture that makes the suite cheap. The client is the module's,
    so no lifespan runs here. The database is the test's, so no test can see
    another test's rows.

    The store is assigned onto ``app.state`` rather than injected, because that
    is the only seam the application offers: ``get_store`` returns
    ``request.app.state.store``, read fresh on every request. Nothing in
    ``dsr`` knows this fixture exists.

    Whatever the test leaves on the application is undone afterwards. Two things
    in particular: ``app.state`` is restored to the value the module client
    started with, and ``dependency_overrides`` is emptied, so a test that
    installs a replacement service and forgets to remove it cannot hand it to
    the next test in the file.
    """
    saved_state = _snapshot_app_state()
    app.state.db = db
    app.state.store = RecordStore(db)
    try:
        yield module_client
    finally:
        _restore_app_state(saved_state)


def _snapshot_app_state() -> dict[str, object]:
    """Read ``app.state`` as a plain dict.

    Starlette's ``State`` object exposes no public way to enumerate its keys, so
    a fixture that promises not to leak has to reach for the dict behind it.
    The alternative is to restore only the two keys this module knows about,
    which silently keeps whatever else the test put there.
    """
    inner = getattr(app.state, "_state", None)
    return dict(inner) if isinstance(inner, dict) else {}


def _restore_app_state(saved: dict[str, object]) -> None:
    inner = getattr(app.state, "_state", None)
    if isinstance(inner, dict):
        inner.clear()
        inner.update(saved)
        return
    for key in ("db", "store"):
        if hasattr(app.state, key):
            delattr(app.state, key)


@pytest.fixture
def http(client: TestClient) -> TestClient:
    """Alias for ``client``.

    Roughly half the modules in this suite call the fixture ``http`` and half
    call it ``client``. Both names resolve here so that a module can adopt this
    file without renaming the fixture its tests already ask for.
    """
    return client
