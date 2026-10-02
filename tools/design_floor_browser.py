#!/usr/bin/env python3
"""The design floor, measured in a real browser.

`tools/check_design_floor.py` reads source and catches the things source can
show. This drives an actual page and catches the rest: contrast ratios, real
tap-target geometry, whether the focus ring is really painted, and whether
keyboard users can reach the controls at all. See
docs/adr/0002-design-floor-is-a-ci-gate.md.

It needs a connected browser-skill extension, so unlike the source check it
cannot run in CI. Run it before landing a feature; CI runs the source check on
every push.

    python tools/design_floor_browser.py --feature wf-033-auto-add-and-...
    python tools/design_floor_browser.py --all
    python tools/design_floor_browser.py --feature ... --json

A measurement that cannot be trusted is worse than no measurement, so every
page is probed against known colours before its results are reported. An
earlier version of this work parsed `oklab()` strings by extracting the first
three numbers and reading them as RGB, which reported a healthy sky-blue badge
as a red one and invented a framework bug that did not exist. The self-check
below exists so that failure cannot recur silently.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BSK = os.environ.get("BSK_BIN") or str(
    Path(os.environ.get("USERPROFILE", Path.home())) / ".local" / "bin" / "bsk.exe"
)

BREAKPOINTS = ((375, 812), (768, 1024), (1024, 768), (1440, 900))
MIN_TAP = 44
MIN_CONTRAST = 4.5
KEYBOARD_STOPS = 40

# The probe converts any CSS colour to sRGB through a canvas. If that
# conversion is wrong, every number below it is wrong, so it is verified
# against colours whose sRGB values are not in dispute before anything else
# runs. Tailwind 4 emits oklab()/oklch(), which cannot be read by regex.
SELF_CHECK_JS = r"""
(() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const toRGB = css => {
    cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = css;
    cx.fillRect(0,0,1,1);
    const d = cx.getImageData(0,0,1,1).data;
    return [d[0], d[1], d[2]];
  };
  const exactCases = [['#ffffff', [255,255,255]], ['#000000', [0,0,0]], ['rgb(255, 0, 0)', [255,0,0]]];
  const exact = exactCases.every(([css, want]) => {
    const got = toRGB(css);
    return want.every((v, i) => Math.abs(v - got[i]) <= 1);
  });
  const sky = toRGB('oklch(68.5% .169 237.323)');
  // A correct sRGB sky-500 has b far greater than r. Reading oklab numbers as
  // RGB inverts this, which is exactly the failure this check exists to catch.
  const skyIsBlue = sky[2] > sky[0] + 40;
  return { exact, sky, skyIsBlue };
})()
"""

MEASURE_JS = r"""
(() => {
  const MIN_TAP = __MIN_TAP__;
  const MIN_CONTRAST = __MIN_CONTRAST__;
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const toRGB = css => {
    cx.clearRect(0,0,1,1); cx.fillStyle = '#000'; cx.fillStyle = css;
    cx.fillRect(0,0,1,1);
    const d = cx.getImageData(0,0,1,1).data;
    return [d[0], d[1], d[2]];
  };
  const alphaOf = css => {
    const m = String(css).match(/\/\s*([0-9.]+)\s*\)\s*$/);
    if (m) return parseFloat(m[1]);
    const p = String(css).match(/rgba?\(([^)]+)\)/);
    if (p) { const parts = p[1].split(','); if (parts.length === 4) return parseFloat(parts[3]); }
    return 1;
  };
  const lin = v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
  const lum = c => 0.2126*lin(c[0]) + 0.7152*lin(c[1]) + 0.0722*lin(c[2]);
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b);
    return +(((Math.max(l1,l2)+0.05)/(Math.min(l1,l2)+0.05)).toFixed(2)); };

  // The effective background is every translucent layer between the element
  // and the nearest opaque one, composited in order. Reading a single
  // backgroundColor is how a 15%-opacity badge gets measured against nothing.
  const effBG = el => {
    const stack = [];
    let n = el;
    while (n && n !== document.documentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') stack.push(c);
      n = n.parentElement;
    }
    let base = [255,255,255];
    for (let i = stack.length - 1; i >= 0; i--) {
      const c = toRGB(stack[i]); const a = alphaOf(stack[i]);
      base = base.map((b,k) => Math.round(c[k]*a + b*(1-a)));
    }
    return base;
  };

  const visible = e => {
    const r = e.getBoundingClientRect(); const st = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };

  const sel = 'a,button,input,select,textarea,[role=button],[role=tab],[role=switch],[tabindex]:not([tabindex="-1"])';
  const inMain = e => e.closest('main') !== null;
  const all = [...document.querySelectorAll(sel)].filter(visible);
  const page = all.filter(inMain);

  // The thing a finger hits is not always the thing that is drawn. A 20x20
  // checkbox inside a 790x44 <label> is a 44px target: clicking anywhere on
  // the label toggles it. Measuring the drawn box alone reports that as a
  // violation, so resolve the effective target: the input's own box, or the
  // label that wraps it, whichever is larger. Verified with
  // document.elementFromPoint at the centre of each.
  const effective = el => {
    const own = el.getBoundingClientRect();
    const label = el.closest('label');
    const box = label ? label.getBoundingClientRect() : own;
    return {
      w: Math.max(own.width, box.width),
      h: Math.max(own.height, box.height),
      wrapped: !!label && (box.height > own.height + 0.5 || box.width > own.width + 0.5)
    };
  };

  const tap = page.map(e => {
    const eff = effective(e);
    return { tag: e.tagName, type: e.getAttribute('type') || '',
             text: (e.innerText || e.getAttribute('aria-label') || e.getAttribute('name') || '').trim().slice(0,40),
             w: +eff.w.toFixed(1), h: +eff.h.toFixed(1), wrapped: eff.wrapped };
  });
  const under = tap.filter(t => t.h < MIN_TAP - 0.5 || t.w < MIN_TAP - 0.5);

  const contrast = [];
  const seen = new Set();
  document.querySelectorAll('main *').forEach(el => {
    if (el.childElementCount !== 0) return;
    const t = (el.innerText || '').trim(); if (!t) return;
    const st = getComputedStyle(el);
    const key = st.color + '|' + st.fontSize + '|' + st.fontWeight;
    if (seen.has(key)) return; seen.add(key);
    const r = ratio(toRGB(st.color), effBG(el));
    const size = parseFloat(st.fontSize) || 0;
    const bold = (parseInt(st.fontWeight, 10) || 400) >= 700;
    // WCAG large text: >=24px, or >=18.66px when bold.
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3.0 : MIN_CONTRAST;
    contrast.push({ text: t.slice(0,34), size: st.fontSize, ratio: r, need,
                    pass: r >= need, cls: (el.className||'').toString().slice(0,60) });
  });

  const emoji = /\p{Extended_Pictographic}/u.test(document.body.innerText);

  // Real horizontal overflow is an element sticking out past the viewport, not
  // a wide scrollWidth. documentElement.scrollWidth counts the scrollbar of any
  // inner scroll container, so a correctly-scrolling wide table inside an
  // overflow-x-auto card reads as a page that overflows when it does not. Look
  // for an element that exceeds the viewport and is not inside something that
  // scrolls, which is the defect a reader would actually see.
  const vw = document.documentElement.clientWidth;
  const insideScroller = (el) => {
    let p = el.parentElement;
    while (p && p !== document.body) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
      p = p.parentElement;
    }
    return false;
  };
  let overflow = false;
  let overflowDetail = null;
  document.querySelectorAll('body *').forEach((el) => {
    if (overflow) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.right <= vw + 1) return;
    if (insideScroller(el)) return;
    overflow = true;
    overflowDetail = `${el.tagName}.${String(el.className || '').replace(/\s+/g, ' ').slice(0, 60)} right=${Math.round(r.right)}`;
  });


  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    pageControls: page.length,
    tapUnderMin: under,
    contrastFailing: contrast.filter(c => !c.pass),
    contrastSampled: contrast.length,
    contrastWorst: contrast.slice().sort((a,b) => a.ratio - b.ratio).slice(0,3),
    emoji: emoji,
    overflow: overflow,
    overflowDetail: overflowDetail
  };
})()
"""

TAB_DISCOVERY_JS = r"""
(() => {
  const main = document.querySelector('main');
  if (!main) return { buttons: [] };
  const btns = [...main.querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  return { buttons: btns.map((b, i) => ({
    i, text: (b.innerText || '').trim().slice(0, 40),
    aria: b.getAttribute('aria-label')
  })) };
})()
"""

CLICK_NTH_JS = r"""
(n) => {
  const m = document.querySelector('main');
  if (!m) return { ok: false, why: 'no main' };
  const b = [...m.querySelectorAll('button')].filter(x => {
    const r = x.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  })[n];
  if (!b) return { ok: false, why: 'button gone' };
  b.click();
  return { ok: true };
}
"""

FOCUS_PROBE_JS = r"""
(() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const st = getComputedStyle(el);
  const own = el.getBoundingClientRect();
  const label = el.closest('label');
  const box = label ? label.getBoundingClientRect() : own;
  return {
    tag: el.tagName,
    text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 36),
    h: +Math.max(own.height, box.height).toFixed(1),
    w: +Math.max(own.width, box.width).toFixed(1),
    outline: st.outlineStyle !== 'none' && parseFloat(st.outlineWidth) > 0,
    ring: st.boxShadow !== 'none',
    focusVisible: el.matches(':focus-visible')
  };
})()
"""


def bsk(*args: str, check: bool = False) -> str:
    exe = shutil.which("bsk") or BSK
    proc = subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    out = (proc.stdout or "").strip()
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or out or "bsk failed").strip())
    return out


def daemon_ok() -> str | None:
    try:
        status = json.loads(bsk("status", "--json"))
    except Exception as exc:  # noqa: BLE001 - any failure means no daemon
        return f"cannot reach the bsk daemon: {exc}"
    if not status.get("browsers"):
        return (
            "the daemon is running but no browser is connected. Open the "
            "browser-skill extension and enable the connection."
        )
    return None


def start_session() -> str:
    return json.loads(bsk("session", "start", "--no-focus", "--json"))["session_id"]


def evaluate(session: str, js: str) -> dict:
    # bsk evaluate serialises the JS value directly: return an object, not a
    # JSON string. A string comes back bare (unquoted), so JSON.parse on our
    # side would see raw text. Wrapping in an object keeps one code path.
    #
    # It also reports a thrown JS error on stderr while still exiting 0, so a
    # silent empty stdout is the only signal that the expression failed.
    payload = f"(() => {{ const __r = {js}; return __r; }})()"
    proc = subprocess.run(
        [shutil.which("bsk") or BSK, "evaluate", "--session", session, payload],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.stderr.strip() and "evaluate threw" in proc.stderr:
        raise RuntimeError(f"JS failed: {proc.stderr.strip()[:300]}")
    raw = (proc.stdout or "").strip()
    if not raw:
        raise RuntimeError("evaluate returned nothing (JS error, or a null result)")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"evaluate did not return JSON: {raw[:300]}") from exc
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def self_check(session: str) -> tuple[bool, str]:
    res = evaluate(session, SELF_CHECK_JS)
    if not res.get("exact"):
        return False, "canvas colour conversion disagrees with known sRGB values"
    if not res.get("skyIsBlue"):
        return False, (
            "oklch(68.5% .169 237.323) did not resolve to a blue; "
            "colour conversion is unreliable, refusing to report contrast"
        )
    return True, "canvas colour conversion verified against known sRGB values"


def app_routes(base: str) -> dict[str, str]:
    with urllib.request.urlopen(f"{base}/api/features", timeout=15) as r:
        reg = json.load(r)
    out = {}
    for f in reg.get("features", []):
        fid = f.get("id") or ""
        if not fid or fid == "core-feature-registry":
            continue
        out[fid] = fid
    return out


def probe_route(session: str, base: str, route: str) -> dict:
    """Every tab, every breakpoint, on one feature route."""
    findings: list[dict] = []
    bsk("navigate", "--session", session, f"{base}/#/{route}")
    time.sleep(2.0)

    discovered = evaluate(session, TAB_DISCOVERY_JS)
    tabs = discovered.get("buttons", [])
    for tab in tabs:
        label = tab.get("text") or tab.get("aria") or f"button {tab['i']}"
        for w, h in BREAKPOINTS:
            bsk("window", "resize", "--session", session, "--width", str(w), "--height", str(h))
            time.sleep(0.4)
            if tab["i"] > 0:
                evaluate(session, f"({CLICK_NTH_JS})({tab['i']})")
                time.sleep(0.7)
            m = evaluate(
                session,
                MEASURE_JS.replace("__MIN_TAP__", str(MIN_TAP)).replace(
                    "__MIN_CONTRAST__", str(MIN_CONTRAST)
                ),
            )
            at = f"{w}x{h}"
            for t in m.get("tapUnderMin", []):
                findings.append(
                    {
                        "kind": "tap-target",
                        "route": route,
                        "tab": label,
                        "viewport": at,
                        "detail": f"{t['tag']}{'[' + t['type'] + ']' if t['type'] else ''} "
                        f"'{t['text']}' {t['w']}x{t['h']}",
                    }
                )
            for c in m.get("contrastFailing", []):
                findings.append(
                    {
                        "kind": "contrast",
                        "route": route,
                        "tab": label,
                        "viewport": at,
                        "detail": f"{c['ratio']}:1 (needs {c['need']}) '{c['text']}' {c['cls']}",
                    }
                )
            if m.get("emoji"):
                findings.append(
                    {
                        "kind": "emoji-as-icon",
                        "route": route,
                        "tab": label,
                        "viewport": at,
                        "detail": "Extended_Pictographic in rendered text",
                    }
                )
            if m.get("overflow"):
                findings.append(
                    {
                        "kind": "horizontal-overflow",
                        "route": route,
                        "tab": label,
                        "viewport": at,
                        "detail": m.get("overflowDetail") or "document scrolls horizontally",
                    }
                )
    return {
        "route": route,
        "findings": findings,
        "tabs": len(tabs),
        "breakpoints": len(BREAKPOINTS),
    }


def keyboard_probe(session: str) -> list[dict]:
    """Tab through the page and check the ring is actually painted."""
    bsk("navigate", "--session", session)
    time.sleep(1.5)
    bsk("window", "resize", "--session", session, "--width", "1440", "--height", "900")
    out = []
    for _ in range(KEYBOARD_STOPS):
        bsk("press", "--session", session, "Tab")
        try:
            step = evaluate(session, FOCUS_PROBE_JS)
        except RuntimeError:
            break
        if not step:
            break
        if not (step.get("outline") or step.get("ring")):
            out.append({"kind": "no-visible-focus", "detail": f"{step['tag']} '{step['text']}'"})
        if step.get("h", 99) < MIN_TAP - 0.5 or step.get("w", 999) < MIN_TAP - 0.5:
            out.append(
                {
                    "kind": "tap-target",
                    "detail": f"keyboard stop {step['tag']} '{step['text']}' "
                    f"{step.get('w')}x{step.get('h')}",
                }
            )
    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--feature", action="append", default=[], help="feature id; repeatable")
    p.add_argument("--all", action="store_true", help="every feature the host reports")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--json", action="store_true")
    p.add_argument("--skip-keyboard", action="store_true")
    args = p.parse_args(argv)

    problem = daemon_ok()
    if problem:
        print(f"cannot run the browser pass: {problem}", file=sys.stderr)
        # 2, not 0. An earlier version returned success here, which meant a
        # missing browser produced a green run that had measured nothing --
        # the same vacuous pass as a guard that checks an empty diff.
        return 2

    try:
        urllib.request.urlopen(f"{args.base}/api/features", timeout=10).close()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot run the browser pass: no app at {args.base}: {exc}", file=sys.stderr)
        return 2

    routes = args.feature or (list(app_routes(args.base)) if args.all else [])
    if not routes:
        print("nothing to check: pass --feature <id> or --all", file=sys.stderr)
        return 2

    session = start_session()
    report = {"base": args.base, "routes": {}, "self_check": None, "keyboard": []}
    try:
        ok, note = self_check(session)
        report["self_check"] = {"ok": ok, "note": note}
        if not ok:
            print(note, file=sys.stderr)
            return 3
        for route in routes:
            report["routes"][route] = probe_route(session, args.base, route)
        if not args.skip_keyboard and routes:
            report["keyboard"] = keyboard_probe(session)
    finally:
        bsk("session", "stop", session)

    total = sum(len(r["findings"]) for r in report["routes"].values()) + len(report["keyboard"])
    report["total_findings"] = total

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        loads = sum(r["tabs"] * r["breakpoints"] for r in report["routes"].values())
        print(f"browser pass: {len(routes)} route(s), {loads} page loads, {total} finding(s)")
        print(f"self-check: {note}")
        for route, res in report["routes"].items():
            if not res["findings"]:
                print(f"  OK   {route}")
            else:
                print(f"  FAIL {route}: {len(res['findings'])} finding(s)")
                for f in res["findings"][:12]:
                    print(
                        f"         {f['kind']} [{f['viewport']}] tab '{f['tab']}': {f['detail'][:96]}"
                    )
        for f in report["keyboard"][:10]:
            print(f"  keyboard: {f['kind']}: {f['detail'][:90]}")

    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
