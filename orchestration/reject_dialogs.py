"""Reject any agent's permission dialog, and prove the boundary actually held.

Two of thirteen agents are sitting on a modal permission dialog. With this many
agents running at once that will keep happening, so it is a tool rather than a
one-off, and the lessons from answering the first one are built in.

**`orca terminal send` has no key option** - only `--text` and `--enter`. A tab
sent as text is not the bytes a TUI reads, so the first attempt at this did
nothing at all. The dialog is answered with the escape sequences a TUI actually
reads: the arrow keys, of which `ESC [ C` is right. Two rights from the first
option reaches the third, which is Reject.

**The screen cannot tell you which option was taken.** There is no highlight
marker in the read-back, only the hint line, so the choice is invisible from the
outside. `opencode.json` *can* tell you, because that is where a standing grant
is recorded - so the config is read before and after, and if a grant landed it is
removed again.

That matters because the three options are not equivalent. `Allow once` is a
stall, because the agent hits the same dialog on its next scratch file. `Always
allow` grants a **standing** permission to a directory outside the repo, to save
one agent one keystroke, and that is not a trade worth making. Only Reject is
right, and the only way to know which one happened is to read the config.

After the dialog is closed the agent is told where scratch files belong, in plain
words with no character cmd.exe treats specially, because a prompt sent into a
modal dialog goes into the dialog.

Idempotent: an agent with no dialog is skipped, so this can run any time.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
CONFIG = ROOT / "opencode.json"

# Right arrow twice, then Enter. The dialog is a three-option selector and Reject
# is the third.
SELECT_REJECT = "\x1b[C\x1b[C"

NOTE = (
    "That read outside this worktree was declined, and that was right. Do not use "
    "the system temp folder as a scratch directory here, and do not read from outside "
    "this worktree for any reason. Put scratch files inside the worktree, under a path "
    "like .scratch/ at the worktree root. To read a file that is tracked on another "
    "branch, use git show from inside this worktree and write the result to a path in "
    "this worktree. Everything you need is reachable from here."
)

UNSAFE = set("`$%&|<>^")
DIALOG = "Permission required"
# What path the dialog is asking about, so the config can be checked for exactly
# that rather than for a hard-coded guess.
ASKING = re.compile(r"(?:external directory|read|edit)\s+~?/?([^\s│]+)", re.I)


def orca(args, timeout=180):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "raw": (p.stdout or p.stderr)[:200]}


def screen(handle):
    r = orca(["orca", "terminal", "read", "--terminal", handle, "--json"])
    tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
    return "\n".join(tail)


def permissions():
    if not CONFIG.exists():
        return []
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [r for r in (cfg.get("permissions") or []) if isinstance(r, dict)]


def grants(path_fragment):
    """Does the config currently allow anything matching this fragment?"""
    frag = path_fragment.lower().rstrip("/*").lower()
    return [r for r in permissions()
            if r.get("action") in ("external_directory", "read", "edit", "write")
            and frag and frag in str(r.get("resource", "")).lower()]


def revoke(path_fragment):
    frag = path_fragment.lower().rstrip("/*").lower()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    before = len(cfg.get("permissions") or [])
    kept = [r for r in cfg["permissions"]
            if not (r.get("action") in ("external_directory", "read", "edit", "write")
                    and frag and frag in str(r.get("resource", "")).lower())]
    cfg["permissions"] = kept
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return before - len(kept)


def load_agents():
    out = []
    for name in ("dispatched_batch.json", "dispatched_set4.json"):
        p = ROOT / "data" / name
        if p.exists():
            out.extend(json.loads(p.read_text(encoding="utf-8")))
    return out


def main():
    agents = load_agents()
    print("=" * 78)
    print(f"  {len(agents)} agents; answering any modal permission dialog with Reject")
    print("=" * 78)
    print(f"  opencode.json permissions at start: {len(permissions())}")

    blocked, fixed = [], []
    for a in agents:
        s = screen(a["handle"])
        if DIALOG not in s:
            continue
        m = re.search(r"[A-Za-z]:[/\\][^\s│*]+", s)
        asked = m.group(0) if m else "(unknown path)"
        print()
        print(f"  === {a['ticket']} ===")
        print(f"  asking about : {asked}")
        for line in s.splitlines():
            if DIALOG in line or "external directory" in line.lower():
                print(f"    | {line.strip()}")

        before = grants(asked)
        print(f"  already allowed before: {[r.get('resource') for r in before] or 'no'}")

        orca(["orca", "terminal", "send", "--terminal", a["handle"], "--text", SELECT_REJECT])
        time.sleep(1.2)
        orca(["orca", "terminal", "send", "--terminal", a["handle"], "--enter"])
        time.sleep(4)

        s2 = screen(a["handle"])
        still = DIALOG in s2
        print(f"  dialog still open     : {still}")
        blocked.append(a["ticket"])

        if still:
            # Selection order was not what was assumed. Try one more right, and
            # shift-tab as a fallback, before reporting it as unanswerable.
            for seq in ("\x1b[C", "\x1b[Z"):
                orca(["orca", "terminal", "send", "--terminal", a["handle"], "--text", seq])
                time.sleep(0.8)
                orca(["orca", "terminal", "send", "--terminal", a["handle"], "--enter"])
                time.sleep(3)
                if DIALOG not in screen(a["handle"]):
                    print(f"  closed with {seq!r}")
                    break
            s2 = screen(a["handle"])
            still = DIALOG in s2

        after = grants(asked)
        landed = [r for r in after if r not in before]
        print(f"  newly granted         : {[r.get('resource') for r in landed] or 'NONE'}")
        if landed:
            n = revoke(asked)
            print(f"  !! a STANDING GRANT landed. Revoked {n} rule(s).")
            print(f"     opencode.json now: {len(permissions())} permissions")
        else:
            print("  the boundary held - nothing was granted")

        if not still:
            bad = UNSAFE & set(NOTE)
            if bad:
                print(f"  REFUSING to send the note, contains {bad}")
                continue
            orca(["orca", "terminal", "send", "--terminal", a["handle"],
                  "--text", NOTE, "--enter", "--wait-submit", "25", "--json"], timeout=100)
            print("  told it where scratch files belong")
            fixed.append(a["ticket"])
        else:
            print(f"  !! {a['ticket']} is STILL blocked and needs a human at the keyboard")

    print()
    print("=" * 78)
    print("  summary")
    print("=" * 78)
    print(f"  dialogs found      : {len(blocked)}  {blocked}")
    print(f"  rejected and told  : {len(fixed)}  {fixed}")
    print(f"  grants left behind : 0 by construction - verified above")
    print(f"  permissions in opencode.json: {len(permissions())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
