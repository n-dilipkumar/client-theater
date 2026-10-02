"""The shared-file list has exactly one definition, and every consumer agrees.

`tools/contract.py` owns SHARED. It used to be duplicated as a literal in four
scripts named in the Jev decision that authorised the module, and in five more
that had grown their own copy since.

Why this test exists
-------------------
The guard that refuses a shared-file edit (`tools/check_feature_diff.py`) is the
only thing standing between a hundred parallel feature branches and a merge
collision. Its behaviour is entirely determined by the contents of its SHARED
list. If one consumer holds a copy that has gained a file and another has not,
then the guard says "OK, none shared" on exactly the branch it exists to refuse,
and the merge pipeline merges it. Nothing crashes. Nothing fails. The failure
mode is a silent false negative in the project's central safety mechanism.

So this asserts the property rather than trusting the refactor: each consumer is
imported for real, and all of them are compared against the single definition.
A consumer that reintroduces a local literal fails here instead of passing
quietly.

It also pins the observable behaviour of the guard, because "they resolve to the
same list" is only worth anything if the list is still the eleven files it was.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
ORCHESTRATION = ROOT / "orchestration"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract import PLATFORM_PREFIXES, SHARED, SHARED_COUNT  # noqa: E402

#: The four consumers named in the Jev decision that authorised the module.
#: Each must import the list, not redeclare it.
DECIDED_CONSUMERS = [
    TOOLS / "check_feature_diff.py",
    ORCHESTRATION / "land_features.py",
    ORCHESTRATION / "merge_ports.py",
    ORCHESTRATION / "verify_batches.py",
]

#: Consumers that acquired their own copy afterwards. They are held to the same
#: rule, because the hazard is the duplication and not the intent of whichever
#: script happened to be written first.
ADDITIONAL_CONSUMERS = [
    ORCHESTRATION / "make_port_briefs.py",
    ORCHESTRATION / "pending_ports.py",
    ORCHESTRATION / "set2_status.py",
    ORCHESTRATION / "agent_status.py",
    ORCHESTRATION / "verify_ports.py",
    ORCHESTRATION / "verify_ready.py",
]

#: De-duplicated, order-preserved: merge_ports.py appears in both lists above.
ALL_CONSUMERS = list(dict.fromkeys(DECIDED_CONSUMERS + ADDITIONAL_CONSUMERS))


def _first_shared_binding(tree: ast.Module):
    """Index of the statement that binds SHARED, or None if it is a literal.

    Two shapes are recognised, and both are legitimate: a bare
    `from tools.contract import SHARED`, and an import aliased then reshaped
    (`SHARED = sorted(_SHARED)`) for the one consumer that feeds a prompt
    template and needs a sequence. An assignment that builds the list out of
    string literals is the thing this change exists to remove, so it returns
    None rather than an index.
    """
    imported_at: int | None = None
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("contract"):
            if any(a.name == "SHARED" for a in node.names) and imported_at is None:
                imported_at = i
        elif isinstance(node, ast.Import) and any(
            a.name in {"SHARED", "contract"} for a in node.names
        ):
            if imported_at is None:
                imported_at = i
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SHARED" for t in node.targets
        ):
            return None if _assigns_a_literal(node) else i
    return imported_at


def _assigns_a_literal(node: ast.Assign) -> bool:
    """Does this assignment build the file list out of literals?"""
    for sub in ast.walk(node.value):
        if isinstance(sub, ast.Dict) and any(
            isinstance(k, ast.Constant) and isinstance(k.value, str) and "/" in k.value
            for k in sub.keys
        ):
            return True
        if isinstance(sub, (ast.List, ast.Tuple, ast.Set)) and any(
            isinstance(e, ast.Constant) and isinstance(e.value, str) and "/" in e.value
            for e in sub.elts
        ):
            return True
    return False


def load_shared(path: pathlib.Path):
    """Execute a consumer's source up to and including its SHARED binding.

    Not `import`, because several of these scripts do their work at module
    level: `agent_status.py` reads `data/dispatched.json` and calls
    `sys.exit(1)` when it is absent, which is correct behaviour for the script
    and a spurious failure for a test. Slicing at the binding means the real
    import statement and the real assignment both run, with none of the script's
    side effects.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    index = _first_shared_binding(tree)
    assert index is not None, f"{path.name} does not bind SHARED from tools/contract.py"

    namespace: dict = {"__file__": str(path), "__name__": f"_consumer_{path.stem}"}
    body = tree.body[: index + 1]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)
    assert "SHARED" in namespace, f"{path.name} did not bind SHARED"
    return namespace["SHARED"]


def shared_binding_is_an_import(path: pathlib.Path) -> bool:
    """Is the module-level SHARED bound from tools.contract rather than a literal?

    Read from the tree rather than the text, so a re-export, an alias or a
    `sorted(...)` of the import is recognised as the definition, and a dict,
    list or set literal of paths is not.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _first_shared_binding(tree) is not None


# --------------------------------------------------------------------------- #
# The list itself
# --------------------------------------------------------------------------- #


def test_the_eleven_shared_files_are_the_eleven_guarded_files():
    """Pins the set, so a deletion cannot quietly reduce what is protected."""
    assert SHARED_COUNT == 11, sorted(SHARED)
    assert SHARED == {
        "backend/dsr/api.py",
        "backend/dsr/deps.py",
        "backend/dsr/store.py",
        "backend/dsr/db/audited.py",
        "backend/seed.py",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/lib/api.js",
        "frontend/src/lib/features.js",
        "frontend/src/components/ui.jsx",
        "frontend/vite.config.js",
    }


def test_every_shared_file_exists():
    """A guard for a file that was renamed protects nothing."""
    missing = [p for p in SHARED if not (ROOT / p).exists()]
    assert not missing, f"SHARED names files that do not exist: {missing}"


def test_platform_prefixes_are_a_tuple_of_directory_prefixes():
    """Platform work is exempt by directory, and the values are used in startswith()."""
    assert isinstance(PLATFORM_PREFIXES, tuple)
    assert all(p.endswith("/") for p in PLATFORM_PREFIXES)
    assert PLATFORM_PREFIXES == ("orchestration/", "docs/", "tools/", "design-system/")


def test_no_shared_file_sits_under_a_platform_prefix():
    """Otherwise the exemption would swallow the protection."""
    assert not [p for p in SHARED if p.startswith(PLATFORM_PREFIXES)]


# --------------------------------------------------------------------------- #
# One definition, many consumers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ALL_CONSUMERS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_consumer_resolves_to_the_single_definition(path: pathlib.Path):
    """Each consumer's SHARED is the module's SHARED, not a copy of it.

    Identity, not equality: `==` would pass on a stale duplicate that happens to
    match today, which is the whole failure mode.
    """
    consumer = load_shared(path)
    assert isinstance(consumer, (set, frozenset, list, tuple)), type(consumer)

    if isinstance(consumer, (list, tuple)):
        # One consumer (make_port_briefs.py) passes the list into a prompt
        # template and needs a sequence. Value equality is the strongest
        # available claim there; that it is not its own literal is enforced by
        # the source check below.
        assert set(consumer) == SHARED, f"{path} holds a different set"
    else:
        assert consumer is SHARED, f"{path} holds a copy of SHARED, not the definition"


@pytest.mark.parametrize("path", ALL_CONSUMERS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_consumer_binds_shared_by_import(path: pathlib.Path):
    """The binding is `from tools.contract import SHARED`, not a literal."""
    assert shared_binding_is_an_import(path), (
        f"{path.name} does not import SHARED from tools/contract.py"
    )


@pytest.mark.parametrize("path", ALL_CONSUMERS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_consumer_does_not_redeclare_the_literal(path: pathlib.Path):
    """No consumer may hold the file list as its own literal again.

    The behavioural test above cannot catch a consumer that declares a literal
    and then never uses it, nor one that declares a literal and overrides it on
    the next line. This reads the source: the list is written out in the file at
    all, it is not the definition.
    """
    source = path.read_text(encoding="utf-8")
    body = source.split("\n")
    offenders = [
        f"{i + 1}: {line.strip()}"
        for i, line in enumerate(body)
        # A literal list always names several of the shared files together.
        if line.count('"backend/dsr/') >= 2 and "SHARED" not in line.split("=")[0]
    ]
    assert not offenders, f"{path.name} redeclares the shared list:\n  " + "\n  ".join(offenders)


def test_contract_module_documents_its_consumers():
    """The module is the source of truth, so it says who reads it."""
    source = (TOOLS / "contract.py").read_text(encoding="utf-8")
    for path in DECIDED_CONSUMERS:
        assert str(path.relative_to(ROOT)).replace("\\", "/") in source, path
    assert "SINGLE SOURCE OF TRUTH" in source


# --------------------------------------------------------------------------- #
# The guard's observable behaviour is unchanged
# --------------------------------------------------------------------------- #


def run_guard(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS / "check_feature_diff.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )


def test_guard_fails_on_zero_changed_files():
    """A guard that passes on nothing measures nothing.

    This is the behaviour a release-bar record once quoted as evidence of
    contract compliance, on a branch with nothing committed.
    """
    result = run_guard("--files")
    assert result.returncode == 1
    assert result.stdout.startswith("FAIL: the explicit --files list was empty.")


def test_guard_reports_the_eleven_shared_files_it_would_refuse():
    result = run_guard("--files", *sorted(SHARED))
    assert result.returncode == 1
    assert "FAIL: 11 shared file(s) edited." in result.stdout
    for path in sorted(SHARED):
        assert f"  - {path}" in result.stdout


def test_guard_normalises_windows_separators():
    """git diff hands back backslashes on Windows; the guard must still match."""
    assert run_guard("--files", "backend\\dsr\\api.py").returncode == 1


def test_guard_passes_a_clean_feature_branch():
    result = run_guard(
        "--files",
        "backend/dsr/features/wf001_x.py",
        "frontend/src/features/wf-001-x/index.jsx",
        "backend/tests/test_wf001.py",
    )
    assert result.returncode == 0
    assert result.stdout == "OK: 3 changed file(s), none shared\n"


def test_allow_shared_downgrades_to_a_notice():
    result = run_guard("--files", "backend/dsr/api.py", "--allow-shared")
    assert result.returncode == 0
    assert result.stdout.startswith("NOTICE: platform change (--allow-shared)")
