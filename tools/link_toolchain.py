#!/usr/bin/env python3
"""Link the shared toolchain into every worktree.

`.venv/`, `node_modules/` and `dist/` are gitignored, so a fresh worktree has no
Python environment and no frontend dependencies. Without this, every sub-agent
fails the moment it runs the test suite:

    ModuleNotFoundError: No module named 'dsr'

Rebuilding a venv per worktree would cost ~10s each and still leave
`node_modules` to install. Instead, link the one environment from the main
checkout. The code being tested stays per-worktree; only the tooling is shared,
so a test run exercises that worktree's source against the shared interpreter.

    python tools/link_toolchain.py            # link all worktrees
    python tools/link_toolchain.py --verify   # report only, change nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
REPO_ID = "8964203a-831a-425f-8fd7-ebc3a0fc2e46"

# (source relative to main checkout, destination relative to a worktree)
LINKS = [
    (MAIN / ".venv", ".venv"),
    (MAIN / "frontend" / "node_modules", "frontend/node_modules"),
]


def orca(*args: str) -> str:
    completed = subprocess.run(
        ["orca", *args], capture_output=True, text=True, errors="replace", timeout=120
    )
    return completed.stdout or ""


def parse_json(output: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def worktree_paths() -> list[Path]:
    payload = parse_json(orca("worktree", "list", "--repo", f"id:{REPO_ID}", "--json"))
    worktrees = (payload.get("result") or {}).get("worktrees") or []
    paths = []
    for worktree in worktrees:
        path = Path(worktree["path"])
        # Skip the main checkout and duplicates that share a path.
        if path.resolve() == MAIN.resolve():
            continue
        if path not in paths:
            paths.append(path)
    return paths


def link(source: Path, target: Path, verify: bool) -> str:
    """Create a directory junction, or report the current state."""
    if not source.exists():
        return f"SKIP  {target.name}: source missing ({source})"
    if target.exists() or target.is_symlink():
        kind = "junction" if target.is_symlink() else "directory"
        if verify:
            return f"ok    {target.name}: already present ({kind})"
        return f"ok    {target.name}: already present"
    if verify:
        return f"MISS  {target.name}: not linked"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(source, target, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without developer mode: fall back to a junction via cmd.
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(source)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        if completed.returncode != 0:
            return f"FAIL  {target.name}: {(completed.stderr or completed.stdout).strip()[:120]}"
    return f"link  {target.name} -> {source}"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="report only, change nothing")
    args = parser.parse_args()

    paths = worktree_paths()
    if not paths:
        print(f"no worktrees found under {WORKSPACE_ROOT}")
        return 1

    print(f"{len(paths)} worktree(s)\n")
    for path in paths:
        print(path.name)
        for source, relative in LINKS:
            print("   " + link(source, path / relative, args.verify))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
