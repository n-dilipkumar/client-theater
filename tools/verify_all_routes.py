#!/usr/bin/env python3
"""Call every route the registry advertises, and fail on a 5xx.

The API half of the release pass. A browser is needed to prove a page renders,
which CI does not have, but the API underneath every page is reachable from a
runner: this starts nothing and simply calls a running app, so it drops into CI
where the browser pass cannot.

A non-2xx is not a fault on its own. A route reading a room that does not exist
answers 404, and a GET on a write route answers 405; both are the API correctly
refusing. What is a fault is a 5xx, or a request that never answers, because
those mean a route the host mounted has no working handler behind it -- and that
is invisible to the import check, which only proves a module loaded.

    python tools/verify_all_routes.py                 # needs a running app on :8000
    python tools/verify_all_routes.py --base URL --json

Exit codes: 0 nothing wrong, 1 at least one route 5xx'd, 2 the app is not up.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

#: Path placeholders the registry advertises. Each maps to a value that will not
#: exist, so a 404 from these is the correct answer rather than a fault. A room id
#: is substituted with a real one, so room-scoped reads exercise real data.
PLACEHOLDERS = {
    "room_id": "{room}",
    "id": "{room}",
    "record_id": "{room}",
    "booking_id": "bk_absent",
    "uid": "bk_absent",
    "meeting_id": "m_absent",
    "run_id": "run_absent",
    "host_id": "h_absent",
    "change_id": "ch_absent",
    "event_id": "ev_absent",
    "link_id": "ln_absent",
    "message_id": "msg_absent",
    "delivery_id": "dlv_absent",
    "request_id": "req_absent",
    "reassignment_id": "ra_absent",
    "history_id": "hist_absent",
    "queue_id": "q_absent",
    "log_id": "log_absent",
    "row_id": "row_absent",
    "row": "row_absent",
    "asset_id": "asset_absent",
    "table_id": "tbl_absent",
    "channel_id": "ch_absent",
    "mapping_id": "map_absent",
    "connection_id": "conn_absent",
    "connector_id": "conn_absent",
    "field_map_id": "fm_absent",
    "event_type_id": "et_absent",
    "flow_id": "fl_absent",
    "meeting_type_id": "mt_absent",
    "template_id": "tpl_absent",
    "generation_id": "gen_absent",
    "key": "absent",
    "policy": "block",
    "kind": "google",
    "step_key": "s1",
    "external_id": "ext_absent",
    "company_key": "co_absent",
    "transaction_id": "tx_absent",
    "form_id": "form_absent",
    "embed_id": "embed_absent",
    "option_id": "opt_absent",
    "schedule_id": "sched_absent",
    "subscription_id": "sub_absent",
    "manifest_id": "man_absent",
    "grant_id": "grant_absent",
    "client_id": "cli_absent",
    "oauth_client_id": "cli_absent",
    "reminder_id": "rem_absent",
    "calendar_id": "cal_absent",
    "location_kind_id": "loc_absent",
    "segment_id": "seg_absent",
    "signal_id": "sig_absent",
    "registration_id": "reg_absent",
    "enrollment_id": "enr_absent",
    "activity_id": "act_absent",
    "document_id": "doc_absent",
    "block_id": "blk_absent",
    "fragment_id": "frag_absent",
    "page_id": "pg_absent",
    "view_id": "view_absent",
    "deal_id": "deal_absent",
    "account_key": "acct_absent",
    "account_id": "acct_absent",
    "workspace_id": "ws_absent",
    "invitation_id": "inv_absent",
    "access_id": "acc_absent",
    "automation_id": "auto_absent",
    "criterion_id": "crit_absent",
    "topic_id": "top_absent",
    "exclusion_id": "exc_absent",
    "play_id": "play_absent",
    "task_id": "task_absent",
    "app_id": "app_absent",
    "prospect_id": "pros_absent",
    "lead_id": "lead_absent",
    "interaction_id": "ix_absent",
    "batch_id": "batch_absent",
    "target_id": "tgt_absent",
    "decision_id": "dec_absent",
    "vendor_id": "vnd_absent",
    "document_gallery_id": "dg_absent",
    "order_id": "ord_absent",
    "purchase_order_id": "po_absent",
    "contract_id": "ct_absent",
    "seat_id": "seat_absent",
    "stage_id": "stg_absent",
    "cell_id": "cell_absent",
    "section_id": "sec_absent",
    "label_id": "lbl_absent",
    "sender_id": "snd_absent",
    "contact_id": "con_absent",
    "campaign_id": "cmp_absent",
    "sequence_id": "seq_absent",
    "step_id": "step_absent",
    "user_id": "usr_absent",
}


def substitute(path: str, room_id: str) -> str:
    out = path
    for name, value in PLACEHOLDERS.items():
        out = out.replace("{" + name + "}", value.replace("{room}", room_id))
    return out


def call(base: str, method: str, path: str) -> tuple[int, str]:
    data = b"{}" if method in ("POST", "PATCH", "PUT") else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:160].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:160]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    try:
        rooms = json.load(urllib.request.urlopen(f"{base}/api/records/room?limit=1", timeout=20))
        room_id = (rooms.get("records") or [{}])[0].get("id", "room_absent")
        reg = json.load(urllib.request.urlopen(f"{base}/api/features", timeout=30))
    except Exception as exc:  # noqa: BLE001
        print(f"no app at {base}: {exc}", file=sys.stderr)
        return 2

    targets: list[tuple[str, str, str]] = []
    for f in reg.get("features", []):
        if not f.get("id") or f["id"] == "core-feature-registry":
            continue
        for r in f.get("routes", []):
            for method in r.get("methods", []):
                targets.append((f["id"], method, r["path"]))

    results = []
    for fid, method, path in targets:
        status, body = call(base, method, substitute(path, room_id))
        results.append({"feature": fid, "method": method, "path": path,
                        "status": status, "body": body})

    faults = [r for r in results if r["status"] == 0 or 500 <= r["status"] < 600]
    by_status: dict[int, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    if args.json:
        print(json.dumps({"checked": len(results), "by_status": by_status,
                          "faults": faults}, indent=2))
    else:
        print(f"routes called: {len(results)} across {len(reg.get('features', [])) - 1} features")
        for status in sorted(by_status):
            print(f"  HTTP {status}: {by_status[status]}")
        if faults:
            print(f"\nFAIL: {len(faults)} route(s) returned 5xx or never answered:")
            for r in faults:
                print(f"  {r['status']} {r['method']:6} {r['path'][:72]}  {r['body'][:100]}")
        else:
            print("\nOK: no route returned 5xx or failed to answer.")

    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
