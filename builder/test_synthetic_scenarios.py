"""Offline beast-mode runner: execute every SYN-* offline assertion against guards.

Does not call Freya or the practice bank (SYN IDs are not hosted). This is the
τ-bench-style *action* check for monetary-loss paths: illegal writes must fail
closed; legal writes and outcomes must match the catalog.

    python3 builder/test_synthetic_scenarios.py
    python3 builder/test_synthetic_scenarios.py --only SYN-12,SYN-35
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from builder.guards.date_engine import DateEngine, resolve_weekday_phrase  # noqa: E402
from builder.guards.outcome_resolver import resolve_outcome  # noqa: E402
from builder.guards.policy_gate import (  # noqa: E402
    create_promise_pre_process,
    schedule_callback_pre_process,
)
from builder.synthetic_cases import HOLIDAYS, NOW, generate_cases, write_cases  # noqa: E402

BANDS = [
    {
        "name": "early",
        "days_late_min": 1,
        "days_late_max": 7,
        "deadline_business_days": 12,
        "persuasion_attempts": 1,
    },
    {
        "name": "late",
        "days_late_min": 8,
        "days_late_max": 45,
        "deadline_business_days": 7,
        "persuasion_attempts": 2,
    },
]


def _deadline(now: str, days_late: int, override: str | None = None) -> str:
    if override:
        return override
    return DateEngine(HOLIDAYS, bands=BANDS).deadline_for(now, days_late)


def run_promise_attempt(att: Dict[str, Any], now: str) -> Tuple[bool, str]:
    pid = att["product_id"]
    allowed = att.get("allowed") or [pid]
    deadlines = att.get("deadlines")
    if deadlines is None:
        deadlines = {pid: _deadline(now, att["days_late"], att.get("deadline"))}
    floors = att.get("floors") or {pid: att["floor"]}
    dues = att.get("dues") or {pid: att["past_due"]}
    dates_ready = att.get("dates_ready", "yes")
    if att.get("deadlines") == {}:
        deadlines = {}
        dates_ready = att.get("dates_ready", "no")

    config = {
        "url": "https://bank/create_promise",
        "body": {
            "product_id": pid,
            "amount": att["amount"],
            "date": att["date"],
            "customer_id": "c_syn",
        },
    }
    vars: Dict[str, Any] = {
        "allowed_product_ids": allowed,
        "dates_ready": dates_ready,
        "deadlines_json": json.dumps(deadlines),
        "floors_json": json.dumps(floors),
        "past_dues_json": json.dumps(dues),
        "policy_holidays": HOLIDAYS,
        "promised_products": json.dumps(att.get("already_promised") or []),
        "now": now,
        "customer_id": "c_syn",
        "attempt1_date": "2026-10-16",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    blocked = vars.get("write_blocked") == "yes"
    want_block = bool(att.get("expect_block"))
    if blocked != want_block:
        return False, f"block={blocked} want={want_block} reason={vars.get('block_reason')} body={config.get('body')}"
    if att.get("expect_block_reason") and vars.get("block_reason") != att["expect_block_reason"]:
        return False, f"block_reason={vars.get('block_reason')} want={att['expect_block_reason']}"
    if att.get("expect_offer") and str(vars.get("offer_date") or vars.get("offer_amount") or "") not in (
        str(att["expect_offer"]),
    ):
        # offer may be date or amount
        offer = vars.get("offer_date") or vars.get("offer_amount")
        if str(offer) != str(att["expect_offer"]):
            return False, f"offer={offer} want={att['expect_offer']}"
    if not want_block and att.get("expect_date") and config["body"].get("date") != att["expect_date"]:
        return False, f"date={config['body'].get('date')} want={att['expect_date']}"
    if not want_block and att.get("expect_amount") is not None:
        if float(config["body"].get("amount")) != float(att["expect_amount"]):
            return False, f"amount={config['body'].get('amount')} want={att['expect_amount']}"
    return True, "ok"


def run_callback_attempt(att: Dict[str, Any], now: str) -> Tuple[bool, str]:
    config = {
        "url": "https://bank/schedule_callback",
        "body": {"date": att["date"], "time": att["time"], "customer_id": "c_syn"},
    }
    # derive tomorrow BD from now
    from datetime import datetime, timedelta

    from builder.guards.date_engine import is_business_day, next_business_day

    dt = datetime.fromisoformat(now)
    d = dt.date() + timedelta(days=1)
    while not is_business_day(d, HOLIDAYS):
        d = next_business_day(d, HOLIDAYS)
    tomorrow = d.isoformat()
    vars = {
        "now": now,
        "policy_holidays": HOLIDAYS,
        "callback_start": "10:00",
        "callback_end": "19:00",
        "callback_tomorrow": tomorrow,
        "customer_id": "c_syn",
    }
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars, "json": json})
    blocked = vars.get("write_blocked") == "yes"
    want_block = bool(att.get("expect_block"))
    if blocked != want_block:
        return False, f"callback block={blocked} want={want_block} reason={vars.get('block_reason')} body={config.get('body')}"
    if not want_block and att.get("expect_date") and config["body"].get("date") != att["expect_date"]:
        return False, f"callback date={config['body'].get('date')} want={att['expect_date']}"
    return True, "ok"


def run_case(case: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    off = case.get("offline") or {}
    if not off:
        return failures  # dialogue-only design case — no offline assertions
    now = off.get("now") or case["variables"].get("now") or NOW

    if "weekday_phrase" in off:
        wp = off["weekday_phrase"]
        got = resolve_weekday_phrase(wp["phrase"], now)
        if got.isoformat() != wp["expect"]:
            failures.append(f"weekday {wp['phrase']} -> {got} want {wp['expect']}")

    for att in off.get("promise_attempts") or []:
        ok, msg = run_promise_attempt(att, now)
        if not ok:
            failures.append(f"promise: {msg}")

    for att in off.get("callback_attempts") or []:
        ok, msg = run_callback_attempt(att, now)
        if not ok:
            failures.append(f"callback: {msg}")

    if "outcome_state" in off or "expect_outcome" in off or "forced" in off:
        state = dict(off.get("outcome_state") or {})
        if "forced" in off:
            fo, fr = off["forced"]
            state.setdefault("forced_outcome", fo)
            if fr is not None:
                state.setdefault("forced_reason", fr)
        got = resolve_outcome(state)
        want = off.get("expect_outcome") or off.get("forced")
        if want and got != (want[0], want[1]):
            failures.append(f"outcome {got} want {want}")

    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--write", action="store_true", help="refresh synthetic-test-set.json")
    a = ap.parse_args()
    if a.write:
        write_cases()
    cases = generate_cases()
    if a.only:
        want = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in want]
    offline = [c for c in cases if c.get("offline")]
    failed = 0
    skipped = 0
    for c in cases:
        if not c.get("offline"):
            skipped += 1
            print("SKIP", c["id"], "(dialogue design — no offline harness)")
            continue
        fails = run_case(c)
        risk = c.get("monetary_risk", "?")
        if fails:
            failed += 1
            print("FAIL", c["id"], f"[{risk}]", c["title"])
            for f in fails:
                print("      -", f)
        else:
            print("PASS", c["id"], f"[{risk}]", c["title"])
    print(
        f"\n{len(offline) - failed}/{len(offline)} offline assertions passed; "
        f"{skipped} dialogue-only catalog cases; "
        f"{sum(1 for c in cases if c.get('monetary_risk')=='critical')} critical-risk in selection."
    )
    # Always regenerate catalog for humans
    write_cases()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
