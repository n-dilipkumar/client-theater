#!/usr/bin/env python3
"""Produce the final indexed workflow corpus, with Jev's dedupe decisions applied.

Output goes to docs/research/digital-sales-room-workflows/:

  INDEX.md              the navigable table, one row per shipped workflow
  workflows.json        machine-readable catalogue for the Orchestrator
  wf/WF-XXX.md          one page per workflow, carrying its research evidence

Ticket ids (`WF-001`...) are assigned here and are the ids used by branches
(`feature/WF-001-<slug>`) and by the build batches, so a workflow keeps the same
identity from research through implementation.

    python tools/build_corpus.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.consolidate import load_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "research" / "digital-sales-room-workflows"
RAW = ROOT / "docs" / "research" / "raw"

DOMAIN_TITLES = {
    "room-experience": "Room experience and content",
    "analytics-intent": "Buyer engagement analytics and intent",
    "crm-integration": "CRM integration and synchronisation",
    "scheduling-meetings": "Scheduling and meetings",
    "security-governance": "Security, access and governance",
    "quoting-proposals": "Pricing, quoting and proposals",
    "automation-engagement": "Automations and engagement",
    "competitive-baseline": "Competitive baseline",
}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    workflows = {w.uid: w for w in load_all()}
    decisions_path = OUT / "dedupe-decisions.json"
    if not decisions_path.is_file():
        print("run tools/dedupe.py first: no dedupe-decisions.json")
        return 1
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))

    # Criticality is a product judgment, so it has its own recorded file rather than
    # a row in the research corpus: docs/research/raw/ is primary-source vendor
    # evidence, and criticality is sourced from no vendor. Whether a workflow is
    # BUILT is deliberately not stored there either - that is measured from git and
    # from the feature registry, and caching it is the stale-dashboard bug.
    crit_path = OUT / "criticality-decisions.json"
    if not crit_path.is_file():
        print(f"run the criticality pass first: no {crit_path.name}")
        return 1
    criticality = json.loads(crit_path.read_text(encoding="utf-8"))
    crit_by_ticket = {d["ticket"]: d for d in criticality["decisions"]}
    if len(crit_by_ticket) != len(criticality["decisions"]):
        print(f"{crit_path.name} lists a ticket more than once")
        return 1

    # Apply merges: a cluster judged "same_workflow" keeps only its canonical entry.
    merged_away: dict[str, str] = {}  # dropped uid -> canonical uid
    merge_notes: list[str] = []
    for decision in decisions:
        if decision["verdict"] != "same_workflow":
            continue
        canonical = decision["canonical"]
        for uid in decision["members"]:
            if uid != canonical and canonical in workflows:
                merged_away[uid] = canonical
        merge_notes.append(
            f"- `{canonical}` absorbs {len(decision['members']) - 1} duplicate(s): "
            + ", ".join(f"`{u}`" for u in decision["members"] if u != canonical)
        )

    kept = [w for uid, w in workflows.items() if uid not in merged_away]

    # Stable ordering: domain in a deliberate reading order, then research number.
    domain_order = list(DOMAIN_TITLES)
    kept.sort(key=lambda w: (domain_order.index(w.domain) if w.domain in domain_order else 99, w.number))

    OUT.mkdir(parents=True, exist_ok=True)
    wf_dir = OUT / "wf"
    wf_dir.mkdir(exist_ok=True)

    entries = []
    for index, workflow in enumerate(kept, start=1):
        ticket = f"WF-{index:03d}"
        crit = crit_by_ticket.get(ticket)
        if crit is None:
            print(
                f"{ticket} ({workflow.name}) has no criticality record. Make the call and "
                f"add it to {crit_path.name}; a workflow with no criticality silently "
                f"disappears from the critical-path count."
            )
            return 1
        aliases = [u for u, c in merged_away.items() if c == workflow.uid]
        entry = {
            "ticket": ticket,
            "slug": workflow.slug,
            "name": workflow.name,
            "domain": workflow.domain,
            "domain_title": DOMAIN_TITLES.get(workflow.domain, workflow.domain),
            "source_uid": workflow.uid,
            "merged_duplicates": aliases,
            "source_count": len(workflow.sources),
            "sources": workflow.sources,
            "criticality": crit["criticality"],
            "criticality_basis": crit["basis"],
            "criticality_rationale": crit["rationale"],
            "path": f"wf/{ticket}.md",
        }
        entries.append(entry)

        # One page per workflow, carrying the research evidence forward.
        body = workflow.body.strip() or "_No prose captured; see the raw domain file._"

        # The page is regenerated in full, but agents tick the build-status boxes
        # and append their implementation notes to it afterwards. Writing a fresh
        # page over the top silently destroyed both - a checklist reset to
        # unticked, and WF-001's recorded notes gone. So the part after the header
        # is preserved and only the build-status block is refreshed.
        page_path = wf_dir / f"{ticket}.md"
        build_status = "\n".join(
            [
                "## Build status",
                "",
                "- [ ] Technical design doc written and Jev-validated",
                "- [ ] Implemented",
                "- [ ] Tests written and passing",
                "- [ ] Reviewer bot passed",
                "- [ ] Jev merge gate passed",
                "- [ ] Verified in localhost browser",
            ]
        )
        existing = page_path.read_text(encoding="utf-8") if page_path.is_file() else ""
        # Carry the whole build-status section forward verbatim, heading included.
        # Agents tick the boxes and append their own notes to it, in whatever order
        # they please - WF-024 has prose before the boxes - so rebuilding it from
        # parts is what dropped the heading and split a checklist.
        preserved_tail = ""
        if "## Build status" in existing:
            preserved_tail = existing[existing.index("## Build status") :].rstrip() + "\n"

        page = "\n".join(
            [
                f"# {ticket} - {workflow.name}",
                "",
                f"- **Domain:** {entry['domain_title']} (`{workflow.domain}`)",
                f"- **Research source:** `docs/research/raw/{workflow.domain}.md` section {workflow.number}",
                f"- **Distinct sources cited:** {len(workflow.sources)}",
                f"- **Criticality:** {crit['criticality']}"
                + (f" ({crit['basis']})" if crit["basis"] else ""),
                f"- **Why:** {crit['rationale']}",
                f"- **Branch:** `feature/{ticket}-{workflow.slug}`",
            ]
            + ([f"- **Merged duplicates:** {', '.join(aliases)}"] if aliases else [])
            + [
                "",
                "## Research evidence",
                "",
                body,
                "",
                "## Sources",
                "",
            ]
            + [f"- <{url}>" for url in workflow.sources]
            + [
                "",
                "---",
                "",
                (preserved_tail or build_status + "\n"),
            ]
        )
        page_path.write_text(page, encoding="utf-8")

    # A record for a ticket that no longer exists is as wrong as a missing one: it
    # means the corpus moved and nobody re-read the judgment alongside it.
    stale = sorted(set(crit_by_ticket) - {e["ticket"] for e in entries})
    if stale:
        print(
            f"{crit_path.name} names {len(stale)} ticket(s) not in the corpus: "
            + ", ".join(stale[:10])
            + (" ..." if len(stale) > 10 else "")
        )
        return 1

    n_critical = sum(1 for e in entries if e["criticality"] == "critical")

    # ---- INDEX.md ------------------------------------------------------- #
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_domain[entry["domain"]].append(entry)

    total_sources = len({u for e in entries for u in e["sources"]})
    lines = [
        "# Digital Sales Room - workflow corpus",
        "",
        "Every workflow below was researched against primary vendor or open-source",
        "sources before being admitted. Each row links to a page carrying that evidence.",
        "",
        "## Summary",
        "",
        f"- **Distinct workflows:** {len(entries)}",
        f"- **Domains:** {len(by_domain)}",
        f"- **Distinct source URLs:** {total_sources}",
        f"- **Duplicates merged after Jev review:** {len(merged_away)}",
        f"- **Critical:** {n_critical}  \\|  **Supplementary:** {len(entries) - n_critical}",
        "",
        "Duplicate handling is recorded rather than silent: Jev was asked, per candidate",
        "cluster, whether the entries were one capability or several. Merges and the",
        "clusters judged distinct are both listed in `dedupe-decisions.json`.",
        "",
        "Criticality is a product judgment, not research evidence, and it is recorded per",
        "workflow in `criticality-decisions.json` with its reasoning. A workflow is",
        "**critical** when removing it leaves something that is not a usable digital sales",
        "room - because it sits on the primary loop (C1: create a room, put content in,",
        "give a buyer access, buyer consumes it, seller learns what happened), because it",
        "is a trust precondition for showing real confidential material to real buyers",
        "(C2), or because other workflows cannot function without it (C3). Everything else",
        "is **supplementary**: the loop closes without it. Whether a workflow is *built* is",
        "deliberately not recorded here, because that is measured from git and the feature",
        "registry rather than carried forward.",
        "",
    ]
    if merge_notes:
        lines += ["## Merged duplicates", ""] + merge_notes + [""]

    for domain, domain_entries in by_domain.items():
        title = DOMAIN_TITLES.get(domain, domain)
        domain_critical = sum(1 for e in domain_entries if e["criticality"] == "critical")
        lines += [
            f"## {title}",
            "",
            f"_({len(domain_entries)} workflows, domain `{domain}`; "
            f"{domain_critical} critical)_",
            "",
            "| Ticket | Workflow | Critical | Sources | Merged |",
            "| --- | --- | --- | --- | --- |",
        ]
        for entry in domain_entries:
            merged = ", ".join(entry["merged_duplicates"]) if entry["merged_duplicates"] else ""
            critical = (
                f"**yes** {entry['criticality_basis']}"
                if entry["criticality"] == "critical"
                else ""
            )
            lines.append(
                f"| [`{entry['ticket']}`]({entry['path']}) | {entry['name']} | {critical} | "
                f"{entry['source_count']} | {merged} |"
            )
        lines.append("")

    (OUT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "workflows.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"distinct workflows : {len(entries)}")
    print(f"duplicates merged : {len(merged_away)}")
    print(f"source URLs       : {total_sources}")
    print(f"critical          : {n_critical}")
    print(f"supplementary     : {len(entries) - n_critical}")
    for domain, domain_entries in by_domain.items():
        print(
            f"  {domain:24s} {len(domain_entries):3d}"
            f"  ({sum(1 for e in domain_entries if e['criticality'] == 'critical')} critical)"
        )
    print(f"\nwrote {(OUT / 'INDEX.md').relative_to(ROOT)}")
    print(f"wrote {(OUT / 'workflows.json').relative_to(ROOT)}")
    print(f"wrote {len(entries)} pages under {(wf_dir).relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
