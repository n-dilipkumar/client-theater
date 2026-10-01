#!/usr/bin/env python3
"""Load every feature page in a real browser and fail if one does not render.

This is the check that found WF-018 and WF-020: two pages threw during render,
and with no error boundary above them React unmounted the whole root, so 34 of
48 pages rendered nothing while the build passed and all 9941 backend tests
passed. Nothing in CI loaded a page, so neither crash was visible to any check.

It needs a connected browser-skill extension, so unlike
``verify_all_routes.py`` it cannot run in CI here. Run it before landing a
feature, and treat a page that does not mount as a release blocker.

    python tools/verify_all_pages.py                 # needs a running app on :8000
    python tools/verify_all_pages.py --base URL --json

The document is reloaded for every page rather than only changing the hash.
Navigating between ``#/`` routes never refetches, so a tab keeps whatever bundle
it cached at session start -- which is how a sweep reports a fix as still broken.

Exit codes: 0 every page rendered, 1 at least one did not, 2 the browser or the
app is unavailable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
(() => {
  const main = document.querySelector('main');
  const txt = document.body.innerText || '';
  return {
    bodyLen: txt.length,
    mainLen: main ? (main.innerText || '').length : 0,
    buttons: document.querySelectorAll('button').length,
    interactive: main
      ? [...main.querySelectorAll('a,button,input,select,textarea,[role=button],[role=tab]')]
          .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length
      : 0,
    heading: (main?.querySelector('h1,h2')?.innerText || '').trim().slice(0, 60)
  };
})()
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--settle", type=float, default=1.6, help="seconds to wait after routing")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    try:
        import design_floor_browser as dfb
    except Exception as exc:  # noqa: BLE001
        print(f"design_floor_browser is not importable: {exc}", file=sys.stderr)
        return 2

    problem = dfb.daemon_ok()
    if problem:
        print(f"cannot run the page sweep: {problem}", file=sys.stderr)
        return 2

    try:
        reg = json.load(urllib.request.urlopen(f"{base}/api/features", timeout=30))
    except Exception as exc:  # noqa: BLE001
        print(f"no app at {base}: {exc}", file=sys.stderr)
        return 2

    ids = [f["id"] for f in reg["features"] if f.get("id") and f["id"] != "core-feature-registry"]
    results: list[dict] = []
    sid = json.loads(dfb.bsk("session", "start", "--no-focus", "--json"))["session_id"]
    try:
        for fid in ids:
            try:
                dfb.bsk("navigate", "--session", sid, f"{base}/?cb={time.time_ns()}")
                time.sleep(1.0)
                dfb.bsk("evaluate", "--session", sid,
                        f"(() => {{ location.hash = '#/{fid}'; return 1; }})()")
                time.sleep(args.settle)
                p = dfb.evaluate(sid, PROBE)
            except Exception as exc:  # noqa: BLE001
                p = {"bodyLen": 0, "mainLen": 0, "buttons": 0, "interactive": 0,
                     "heading": "", "err": str(exc)[:160]}
            # BLANK: the document is nearly empty, so the tree did not mount.
            # SPARSE: it mounted but rendered almost nothing.
            status = "BLANK" if p["bodyLen"] < 40 else ("SPARSE" if p["mainLen"] < 20 else "ok")
            results.append({"id": fid, "status": status, "bodyLen": p["bodyLen"],
                            "mainLen": p["mainLen"], "buttons": p["buttons"],
                            "interactive": p["interactive"], "heading": p["heading"]})
    finally:
        dfb.bsk("session", "stop", sid)

    bad = [r for r in results if r["status"] != "ok"]
    if args.json:
        print(json.dumps({"checked": len(results), "bad": bad}, indent=2))
    else:
        print(f"pages loaded: {len(results)}")
        for r in results:
            if r["status"] != "ok":
                print(f"  {r['status']:6} {r['id']:54} main={r['mainLen']:5} btn={r['buttons']:3}")
        if bad:
            print(f"\nFAIL: {len(bad)} of {len(results)} pages did not render.")
        else:
            print(f"\nOK: all {len(results)} feature pages rendered.")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
