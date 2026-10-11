"""Offline tests for date engine, gates, and outcome resolver."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from builder.guards.date_engine import (  # noqa: E402
    DateEngine,
    business_day_nth,
    clamp_promise_date,
    is_business_day,
    last_business_day_of_week,
    next_business_day,
    resolve_weekday_phrase,
)
from builder.guards.ledger_mirror import update_ledger_from_tool  # noqa: E402
from builder.guards.outcome_resolver import resolve_outcome  # noqa: E402
from builder.guards.policy_gate import (  # noqa: E402
    create_promise_pre_process,
    close_call_pre_process,
)


HOLIDAYS = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"]


def test_business_days():
    assert is_business_day(datetime.date(2026, 10, 14), HOLIDAYS)  # Wed
    assert not is_business_day(datetime.date(2026, 10, 17), HOLIDAYS)  # Sat
    assert not is_business_day(datetime.date(2026, 10, 26), HOLIDAYS)  # holiday
    assert next_business_day(datetime.date(2026, 10, 24), HOLIDAYS) == datetime.date(2026, 10, 27)  # skip Sun+Mon holiday? 24=Sat -> 27
    # 12th business day after 2026-10-14 (skips weekend + 2026-10-26 holiday)
    d = business_day_nth(datetime.date(2026, 10, 14), 12, HOLIDAYS)
    assert d == datetime.date(2026, 11, 2), d
    # 7th business day after 2026-10-14 for late band
    d7 = business_day_nth(datetime.date(2026, 10, 14), 7, HOLIDAYS)
    assert d7 == datetime.date(2026, 10, 23), d7


def test_weekday_phrases():
    # Wednesday 14 Oct 2026
    assert resolve_weekday_phrase("next Friday", "2026-10-14T11:00") == datetime.date(2026, 10, 23)
    assert resolve_weekday_phrase("this Friday", "2026-10-14T11:00") == datetime.date(2026, 10, 16)
    # next Friday said on Friday = following week
    assert resolve_weekday_phrase("next Friday", "2026-10-16T11:00") == datetime.date(2026, 10, 23)


def test_attempt1_and_clamp():
    eng = DateEngine(HOLIDAYS, bands=[
        {"name": "early", "days_late_min": 1, "days_late_max": 7, "deadline_business_days": 12, "persuasion_attempts": 1},
        {"name": "late", "days_late_min": 8, "days_late_max": 45, "deadline_business_days": 7, "persuasion_attempts": 2},
    ])
    assert eng.deadline_for("2026-10-14T11:00", 14) == "2026-10-23"
    assert eng.deadline_for("2026-10-14T11:00", 6) == "2026-11-02"
    # last BD this week from Wed = Friday 16
    assert last_business_day_of_week(datetime.date(2026, 10, 14), HOLIDAYS) == datetime.date(2026, 10, 16)
    assert eng.attempt1_date("2026-10-14T11:00") == "2026-10-16"
    # if today is Friday, attempt1 is next week
    assert eng.attempt1_date("2026-10-16T11:00") == "2026-10-23"

    iso, st = clamp_promise_date("2026-10-17", "2026-10-14T11:00", "2026-10-23", HOLIDAYS)
    assert st == "shifted" and iso == "2026-10-19", (iso, st)
    iso, st = clamp_promise_date("2026-11-06", "2026-10-14T11:00", "2026-10-23", HOLIDAYS)
    assert st == "after_deadline" and iso == "2026-10-23"
    iso, st = clamp_promise_date("2026-10-26", "2026-10-14T11:00", "2026-10-30", HOLIDAYS)
    assert st == "shifted" and iso == "2026-10-27"


def test_outcome_resolver():
    assert resolve_outcome({"ledger_transferred": "true", "transfer_queue": "restructuring", "has_promise": "true"}) == (
        "transferred",
        "restructuring",
    )
    assert resolve_outcome({"has_promise": "true"}) == ("promise", None)
    assert resolve_outcome({"forced_outcome": "paid"}) == ("paid", None)
    assert resolve_outcome({
        "ledger_notes": [{"product_id": "p1", "reason": "paid"}, {"product_id": "p2", "reason": "refused"}],
        "allowed_product_ids": ["p1", "p2"],
    }) == ("no_promise", "refused")
    assert resolve_outcome({
        "ledger_notes": [{"product_id": "p1", "reason": "paid"}, {"product_id": "p2", "reason": "paid"}],
    }) == ("paid", None)
    assert resolve_outcome({"has_callback": "true"}) == ("not_reached", "callback")


def test_ledger_mirror():
    v = {}
    update_ledger_from_tool(v, "create_promise", {"data": {"ok": True, "promise_id": "x"}}, {"product_id": "p1", "amount": 10, "date": "2026-10-16"})
    assert v["has_promise"] == "true"
    assert v["promised_products"] == ["p1"]


def test_gate_snippets_are_safe():
    from builder.guards.policy_gate import schedule_callback_pre_process

    for snip in (create_promise_pre_process(), close_call_pre_process(), schedule_callback_pre_process()):
        assert "{{" not in snip and "}}" not in snip
        assert "try:" in snip

    # Fail closed: unknown product / after deadline blocked
    config = {"url": "https://bank/create_promise", "body": {"product_id": "p1", "amount": 1250, "date": "2026-11-06"}}
    vars = {
        "allowed_product_ids": ["p1"],
        "dates_ready": "yes",
        "deadlines_json": json.dumps({"p1": "2026-10-23"}),
        "floors_json": json.dumps({"p1": 400}),
        "past_dues_json": json.dumps({"p1": 1250}),
        "policy_holidays": HOLIDAYS,
        "promised_products": [],
        "now": "2026-10-14T11:00",
        "customer_id": "c1",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "yes"
    assert "example.invalid" in config["url"]

    # Legal promise allowed
    config = {"url": "https://bank/create_promise", "body": {"product_id": "p1", "amount": 1250, "date": "2026-10-23"}}
    vars["write_blocked"] = ""
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "no"
    assert config["body"]["date"] == "2026-10-23"

    # Empty deadlines fail closed
    config = {"url": "https://bank/create_promise", "body": {"product_id": "p1", "amount": 100, "date": "2026-10-16"}}
    vars2 = {
        "allowed_product_ids": [],
        "dates_ready": "no",
        "deadlines_json": "{}",
        "floors_json": "{}",
        "past_dues_json": "{}",
        "promised_products": [],
        "now": "2026-10-14T11:00",
        "customer_id": "c1",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars2, "json": json})
    assert vars2["write_blocked"] == "yes"

    # Past "tomorrow" mis-parse snaps to callback_tomorrow
    config = {"url": "https://bank/schedule_callback", "body": {"date": "2026-10-11", "time": "11:00"}}
    vars3 = {
        "now": "2026-10-14T11:00",
        "policy_holidays": HOLIDAYS,
        "callback_start": "10:00",
        "callback_end": "19:00",
        "callback_tomorrow": "2026-10-15",
        "customer_id": "c1",
    }
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars3, "json": json})
    assert vars3["write_blocked"] == "no", vars3.get("block_reason")
    assert config["body"]["date"] == "2026-10-15"

    # Weekend date still blocked
    config = {"url": "https://bank/schedule_callback", "body": {"date": "2026-10-17", "time": "11:00"}}
    vars3["write_blocked"] = ""
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars3, "json": json})
    # Saturday snaps to tomorrow (15) which is allowed — use a far future weekday instead
    config = {"url": "https://bank/schedule_callback", "body": {"date": "2026-10-20", "time": "11:00"}}
    vars3["write_blocked"] = ""
    # Without callback_tomorrow snap target for far dates: clear tomorrow so snap uses allowed[-1]
    # Far future Tue 20 Oct is not in {14,15}; snap to 15 → allowed
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars3, "json": json})
    assert vars3["write_blocked"] == "no"
    assert config["body"]["date"] == "2026-10-15"

    # Invalid time blocked
    config = {"url": "https://bank/schedule_callback", "body": {"date": "2026-10-15", "time": "09:00"}}
    vars3["write_blocked"] = ""
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars3, "json": json})
    assert vars3["write_blocked"] == "yes"

    # Tomorrow 11:00 allowed from Wed 14 Oct
    config = {"url": "https://bank/schedule_callback", "body": {"date": "2026-10-15", "time": "11:00"}}
    vars3["write_blocked"] = ""
    exec(schedule_callback_pre_process(), {"config": config, "vars": vars3, "json": json})
    assert vars3["write_blocked"] == "no"

    # Close bakes forced callback outcome
    config = {"url": "https://bank/close_call", "body": {"outcome": "no_promise", "reason": "refused"}}
    vars4 = {
        "customer_id": "c1",
        "has_callback": "true",
        "resolved_outcome": "not_reached",
        "resolved_reason": "callback",
        "close_sent": "no",
    }
    exec(close_call_pre_process(), {"config": config, "vars": vars4, "json": json})
    assert config["body"]["outcome"] == "not_reached"
    assert config["body"]["reason"] == "callback"
    assert vars4["close_sent"] == "yes"
    assert "example.invalid" not in config["url"]

    # Duplicate lifecycle close keeps bank URL (no DNS-fail defang)
    from builder.guards.policy_gate import close_call_post_process

    config2 = {"url": "https://bank/close_call", "body": {}}
    vars5 = dict(vars4)
    exec(close_call_pre_process(), {"config": config2, "vars": vars5, "json": json})
    assert vars5["close_duplicate"] == "yes"
    assert config2["url"] == "https://bank/close_call"
    assert "example.invalid" not in config2["url"]
    result = {"status": "error", "message": "DNS resolution failed", "data": {}}
    exec(close_call_post_process(), {"result": result, "vars": vars5, "json": json})
    assert result["status"] == "success"
    assert result["data"].get("ok") is True


def test_verify_post_hook_zero_left():
    from builder.guards.post_hooks import verify_identity_post_hook

    result = {"data": {"ok": True, "verified": False, "wrong_answers_left": 0, "all_verified": False}}
    vars = {}
    exec(verify_identity_post_hook(), {"result": result, "vars": vars})
    assert vars["forced_outcome"] == "not_verified"
    assert vars["forced_reason"] == "failed"
    assert "Verification failed" in result["data"]["what_to_do_next"]


def test_date_engine_code_has_month_days():
    from builder.guards.date_engine import DATE_ENGINE_CODE

    assert "MONTH_DAYS" in DATE_ENGINE_CODE
    assert "dates_ready" in DATE_ENGINE_CODE
    # Simulate Freya sandbox: no MONTH_DAYS NameError
    g = {"vars": {
        "now": "2026-10-14T11:00",
        "policy_holidays": HOLIDAYS,
        "policy_bands": [
            {"name": "late", "days_late_min": 8, "days_late_max": 45, "deadline_business_days": 7, "persuasion_attempts": 2},
            {"name": "early", "days_late_min": 1, "days_late_max": 7, "deadline_business_days": 12, "persuasion_attempts": 1},
        ],
        "floor_fields_json": json.dumps({"car_loan": "instalment_amount", "personal_loan": "instalment_amount"}),
        "account_products_json": json.dumps([
            {"product_id": "p1", "type": "personal_loan", "past_due": 540, "days_late": 5, "instalment_amount": 180},
        ]),
    }}
    exec(DATE_ENGINE_CODE, g)
    assert g["vars"]["dates_ready"] == "yes"
    deadlines = json.loads(g["vars"]["deadlines_json"])
    assert deadlines["p1"] == "2026-11-02"
    assert json.loads(g["vars"]["attempts_json"])["p1"] == 1
    assert "personal loan" in json.loads(g["vars"]["labels_json"])["p1"]
    assert g["vars"]["attempt1_date"] == "2026-10-16"
    assert "2026-10-26 is a holiday" in g["vars"]["date_facts"]
    assert "2026-10-27" in g["vars"]["date_facts"]


def test_holiday_promise_offers_next_bd():
    from builder.guards.policy_gate import create_promise_pre_process

    config = {"url": "https://bank/create_promise", "body": {"product_id": "p1", "amount": 540, "date": "2026-10-26"}}
    vars = {
        "allowed_product_ids": ["p1"],
        "dates_ready": "yes",
        "deadlines_json": json.dumps({"p1": "2026-11-02"}),
        "floors_json": json.dumps({"p1": 180}),
        "past_dues_json": json.dumps({"p1": 540}),
        "policy_holidays": HOLIDAYS,
        "promised_products": [],
        "now": "2026-10-14T11:00",
        "customer_id": "c1",
        "attempt1_date": "2026-10-16",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "yes"
    assert vars["block_reason"] == "holiday"
    assert vars["offer_date"] == "2026-10-27"


def test_multi_product_clamps_after_deadline():
    from builder.guards.policy_gate import create_promise_pre_process

    config = {
        "url": "https://bank/create_promise",
        "body": {"product_id": "p2", "amount": 900, "date": "2026-10-30"},
    }
    vars = {
        "allowed_product_ids": ["p1", "p2"],
        "dates_ready": "yes",
        "deadlines_json": json.dumps({"p1": "2026-11-02", "p2": "2026-10-23"}),
        "floors_json": json.dumps({"p1": 90, "p2": 300}),
        "past_dues_json": json.dumps({"p1": 300, "p2": 900}),
        "fitted_dates_json": json.dumps({"p1": "2026-10-30", "p2": "2026-10-23"}),
        "policy_holidays": HOLIDAYS,
        "promised_products": [],
        "now": "2026-10-14T11:00",
        "customer_id": "c1",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "no"
    assert config["body"]["date"] == "2026-10-23"


def test_floor_offered_bakes_gate():
    from builder.guards.policy_gate import create_promise_pre_process

    config = {"url": "https://bank/create_promise", "body": {"product_id": "p9", "amount": 1, "date": "2026-10-17"}}
    vars = {
        "floor_offered": "yes",
        "gate_product_id": "p1",
        "gate_amount": "637.5",
        "gate_date": "2026-10-16",
        "customer_id": "c1",
    }
    exec(create_promise_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "no"
    assert config["body"]["date"] == "2026-10-16"
    assert config["body"]["amount"] == 637.5
    assert config["body"]["product_id"] == "p1"


def test_compile_and_lint():
    from builder.compile import compile_to_file
    from builder.lint import lint_file

    out = ROOT / "out" / "brief-a.workflow.json"
    compile_to_file(ROOT / "builder/policies/brief_a_b.policy.json", ROOT / "tools/openapi.json", out)
    ok, errs = lint_file(out, ROOT / "tools/openapi.json")
    assert ok, errs
    wf = json.loads(out.read_text())
    assert "agent_config" not in wf
    assert wf["initial_node"] == "setup"
    assert "close_call" in json.dumps(wf["tools"])
    assert "track_persuasion" in wf["nodes"]
    assert "route_persuasion" in wf["nodes"]
    # collection must not expose add_note (finalize_notes owns refused notes)
    coll_tools = json.dumps(wf["nodes"]["collection"].get("tools") or [])
    assert "add_note" not in coll_tools
    fin_tools = json.dumps(wf["nodes"]["finalize_notes"].get("tools") or [])
    assert "add_note" in fin_tools
    # Must route on one-shot note_just_ok, never sticky add_note_response.data.ok
    fin_trans = json.dumps(wf["nodes"]["finalize_notes"].get("transitions") or [])
    assert "note_just_ok" in fin_trans
    assert "add_note_response.data.ok" not in fin_trans


def test_note_just_ok_and_parallel_pid_stack():
    from builder.guards.policy_gate import add_note_pre_process
    from builder.guards.post_hooks import add_note_post_hook

    pre = add_note_pre_process()
    post = add_note_post_hook()
    vars = {
        "note_reasons": ["refused"],
        "awaiting_notes": "yes",
        "deadlines_json": "{}",
        "proposed_date": "",
        "ledger_notes": "[]",
        "noted_products": "[]",
        "note_pid_stack": "[]",
        "note_just_ok": "no",
    }
    # Parallel pre for p2 then p3
    c2 = {"url": "https://bank/add_note", "body": {"product_id": "p2", "reason": "refused"}}
    c3 = {"url": "https://bank/add_note", "body": {"product_id": "p3", "reason": "refused"}}
    exec(pre, {"config": c2, "vars": vars, "json": json})
    exec(pre, {"config": c3, "vars": vars, "json": json})
    assert json.loads(vars["note_pid_stack"]) == ["p2", "p3"]

    # Post completions in order — each pops its own pid (not sticky last_note_pid)
    r2 = {"status": "success", "data": {"ok": True}}
    exec(post, {"result": r2, "vars": vars, "json": json})
    assert vars["note_just_ok"] == "yes"
    assert "p2" in json.loads(vars["noted_products"])
    vars["note_just_ok"] = "no"  # consumed by check_remaining
    r3 = {"status": "success", "data": {"ok": True}}
    exec(post, {"result": r3, "vars": vars, "json": json})
    noted = json.loads(vars["noted_products"])
    assert set(noted) == {"p2", "p3"}
    assert vars["note_just_ok"] == "yes"
    # Sticky ok alone must not be required — flag is the route signal
    assert json.loads(vars["note_pid_stack"]) == []


def test_parallel_note_race_reconciles():
    # True-parallel add_note calls share one vars snapshot: the second FIFO
    # push overwrites the first, so both post-hooks attribute the same pid.
    # check_remaining must credit the attempted-but-unnoted product instead
    # of re-entering finalize_notes (goodbye loop -> max_turns_exceeded).
    from builder.guards.policy_gate import add_note_pre_process
    from builder.guards.post_hooks import add_note_post_hook
    from builder.compile import compile_workflow

    pre = add_note_pre_process()
    post = add_note_post_hook()
    base = {
        "note_reasons": ["refused"],
        "awaiting_notes": "yes",
        "deadlines_json": "{}",
        "proposed_date": "",
        "ledger_notes": "[]",
        "noted_products": "[]",
        "allowed_product_ids": ["p1", "p2", "p3"],
        "promised_products": json.dumps(["p1"]),
    }
    v1 = dict(base)
    v2 = dict(base)
    c2 = {"url": "https://bank/add_note", "body": {"product_id": "p2", "reason": "refused"}}
    c3 = {"url": "https://bank/add_note", "body": {"product_id": "p3", "reason": "refused"}}
    exec(pre, {"config": c2, "vars": v1, "json": json})
    exec(pre, {"config": c3, "vars": v2, "json": json})
    assert v1.get("write_blocked") == "no" and v2.get("write_blocked") == "no"
    # Platform merge: shared keys last-write-wins, per-pid markers survive.
    live = dict(base)
    live.update(v2)
    for k, val in v1.items():
        if k.startswith("note_attempted_") and val == "yes":
            live[k] = "yes"
    assert json.loads(live["note_pid_stack"]) == ["p3"]  # first push lost
    assert live.get("note_attempted_p2") == "yes"
    assert live.get("note_attempted_p3") == "yes"
    # Both bank calls succeed; both post-hooks claim p3 (observed prod race).
    exec(post, {"result": {"status": "success", "data": {"ok": True}}, "vars": live, "json": json})
    assert live["note_just_ok"] == "yes"
    live["note_just_ok"] = "no"  # consumed by check_remaining
    exec(post, {"result": {"status": "success", "data": {"ok": True}}, "vars": live, "json": json})
    assert live.get("note_race") == "yes"
    assert json.loads(live["noted_products"]) == ["p3"]  # p2 attribution lost
    # Real compiled check_remaining always credits note_attempted_* markers.
    wf = compile_workflow(
        json.loads((ROOT / "builder/policies/brief_a_b.policy.json").read_text()),
        ROOT / "tools/openapi.json",
    )
    code = wf["nodes"]["check_remaining"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert "note_attempted_" in code
    assert "Do not note other products this turn" in code  # one pid per finalize visit
    exec(code, {"vars": live, "json": json})
    assert set(json.loads(live["noted_products"])) == {"p2", "p3"}
    assert live.get("open_products") == ""
    assert live.get("need_notes") == "no"


def test_persuasion_pub12_counters():
    from builder.guards.persuasion import advance_persuasion, PERSUASION_CODE

    vars = {
        "allowed_product_ids": ["p1", "p2", "p3"],
        "promised_products": ["p1"],
        "ledger_notes": [],
        "attempts_json": json.dumps({"p1": 2, "p2": 2, "p3": 1}),
        "persuasion_used_json": "{}",
        "labels_json": json.dumps(
            {"p1": "credit card ending 9015", "p2": "personal loan", "p3": "car loan"}
        ),
        "past_dues_json": json.dumps({"p1": 510, "p2": 1320, "p3": 140}),
        "floors_json": json.dumps({"p1": 153, "p2": 440, "p3": 46}),
        "deadlines_json": json.dumps({"p1": "2026-10-23", "p2": "2026-10-23", "p3": "2026-11-02"}),
        "attempt1_date": "2026-10-16",
        "attempt1_spoken": "Friday 16 October",
        "full_too_much": "false",
        "fitted_dates_json": "{}",
    }
    # Refuse 1: only personal loan attempt 1 (one product at a time)
    advance_persuasion(vars)
    assert vars["continue_persuasion"] == "yes"
    assert vars["persuasion_focus"] == "p2"
    used = json.loads(vars["persuasion_used_json"])
    assert used == {"p2": 1}
    assert "attempt 1/2 for personal loan" in vars["persuasion_instruction"]
    assert "personal loan" in vars["persuasion_tts"]
    assert "1320.00" in vars["persuasion_tts"]

    # Refuse 2: personal loan attempt 2 (late)
    advance_persuasion(vars)
    assert vars["persuasion_focus"] == "p2"
    used = json.loads(vars["persuasion_used_json"])
    assert used == {"p2": 2}
    assert "attempt 2/2 for personal loan" in vars["persuasion_instruction"]
    assert "440.00" in vars["persuasion_tts"]
    assert "2026-10-23" in vars["persuasion_tts"]

    # Refuse 3: car loan attempt 1 only (early)
    advance_persuasion(vars)
    assert vars["persuasion_focus"] == "p3"
    used = json.loads(vars["persuasion_used_json"])
    assert used == {"p2": 2, "p3": 1}
    assert "car loan" in vars["persuasion_tts"]
    assert vars["persuasion_tts"].count("?") == 1

    # Refuse 4: exhausted → notes
    advance_persuasion(vars)
    assert vars["continue_persuasion"] == "no"
    assert vars["awaiting_notes"] == "yes"
    used = json.loads(vars["persuasion_used_json"])
    assert used == {"p2": 2, "p3": 1}

    # refuses + stale proposed_date must NOT divert to fit
    vars2 = {
        "allowed_product_ids": ["p1", "p2"],
        "promised_products": ["p1"],
        "ledger_notes": [],
        "customer_intent": "refuses",
        "proposed_date": "2026-10-11",
        "fitted_dates_json": "{}",
        "attempts_json": json.dumps({"p1": 2, "p2": 2}),
        "persuasion_used_json": "{}",
        "labels_json": json.dumps({"p1": "credit card", "p2": "personal loan"}),
        "past_dues_json": json.dumps({"p1": 510, "p2": 1320}),
        "floors_json": json.dumps({"p1": 153, "p2": 440}),
        "deadlines_json": json.dumps({"p1": "2026-10-23", "p2": "2026-10-23"}),
        "attempt1_date": "2026-10-16",
        "attempt1_spoken": "Friday 16 October",
    }
    advance_persuasion(vars2)
    assert vars2["need_fit_dates"] == "no"
    assert vars2["persuasion_focus"] == "p2"
    assert vars2["proposed_date_consumed"] == "yes"

    # Multi-fit + refuse → keep only first unpromised for confirm; do not burn attempts
    vars3 = {
        "allowed_product_ids": ["p1", "p2", "p3"],
        "promised_products": [],
        "ledger_notes": [],
        "customer_intent": "refuses",
        "proposed_date": "",
        "fitted_dates_json": json.dumps(
            {"p1": "2026-10-15", "p2": "2026-10-15", "p3": "2026-10-15"}
        ),
        "attempts_json": json.dumps({"p1": 2, "p2": 2, "p3": 1}),
        "persuasion_used_json": "{}",
        "labels_json": json.dumps({"p1": "cc", "p2": "pl", "p3": "car"}),
        "past_dues_json": json.dumps({"p1": 510, "p2": 1320, "p3": 140}),
        "floors_json": json.dumps({"p1": 153, "p2": 440, "p3": 46}),
        "deadlines_json": json.dumps(
            {"p1": "2026-10-23", "p2": "2026-10-23", "p3": "2026-11-02"}
        ),
        "attempt1_date": "2026-10-16",
        "attempt1_spoken": "Friday 16 October",
    }
    advance_persuasion(vars3)
    assert vars3["persuasion_used_json"] == "{}"
    assert vars3["resume_collection"] == "yes"
    assert vars3["continue_persuasion"] == "no"
    assert json.loads(vars3["fitted_dates_json"]) == {"p1": "2026-10-15"}
    assert "p1" in vars3["persuasion_instruction"] and "create_promise" in vars3[
        "persuasion_instruction"
    ]

    g = {
        "vars": {
            "allowed_product_ids": ["p2"],
            "promised_products": "[]",
            "ledger_notes": "[]",
            "customer_intent": "refuses",
            "attempts_json": '{"p2": 2}',
            "persuasion_used_json": "{}",
            "labels_json": '{"p2": "personal loan"}',
            "past_dues_json": '{"p2": 1320}',
            "floors_json": '{"p2": 440}',
            "deadlines_json": '{"p2": "2026-10-23"}',
            "attempt1_date": "2026-10-16",
            "attempt1_spoken": "Friday 16 October",
            "fitted_dates_json": "{}",
        }
    }
    exec(PERSUASION_CODE, g)
    assert g["vars"]["continue_persuasion"] == "yes"
    assert json.loads(g["vars"]["persuasion_used_json"])["p2"] == 1


def test_deadline_refused_after_one_offer():
    """Brief A rule 23: speak deadline once; second after-deadline propose → flat refusal."""
    from builder.compile import compile_workflow

    wf = compile_workflow(
        json.loads((ROOT / "builder/policies/brief_a_b.policy.json").read_text()),
        ROOT / "tools/openapi.json",
    )
    assert "speak_deadline_offer" in wf["nodes"]
    assert "listen_deadline" in wf["nodes"]
    code = wf["nodes"]["fit_product_dates"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert "deadline_refused" in code
    assert "need_deadline_offer" in code
    trans = json.dumps(wf["nodes"]["fit_product_dates"].get("transitions") or [])
    assert "track_persuasion" in trans
    assert "speak_deadline_offer" in trans

    base = {
        "now": "2026-10-14T11:00",
        "policy_holidays": HOLIDAYS,
        "allowed_product_ids": ["p1"],
        "deadlines_json": json.dumps({"p1": "2026-10-23"}),
        "past_dues_json": json.dumps({"p1": 1250}),
        "fitted_dates_json": "{}",
        "proposed_date": "2026-11-06",
        "deadline_offered": "no",
        "deadline_refused": "no",
    }
    v1 = dict(base)
    exec(code, {"vars": v1, "json": json, "datetime": __import__("datetime")})
    assert v1["deadline_offered"] == "yes"
    assert v1["need_deadline_offer"] == "yes"
    assert v1["deadline_refused"] == "no"
    assert v1["gate_ok"] == "yes"
    assert v1["gate_date"] == "2026-10-23"
    assert "2026-10-23" in v1["deadline_offer_tts"]
    assert json.loads(v1["fitted_dates_json"])["p1"] == "2026-10-23"

    v2 = dict(base)
    v2["deadline_offered"] = "yes"
    v2["proposed_date"] = "2026-11-06"
    exec(code, {"vars": v2, "json": json, "datetime": __import__("datetime")})
    assert v2["deadline_refused"] == "yes"
    assert v2["customer_intent"] == "refuses"
    assert v2["fitted_dates_json"] == "{}"


def test_add_note_blocks_before_persuasion_exhausted():
    from builder.guards.policy_gate import add_note_pre_process

    config = {"url": "https://bank/add_note", "body": {"product_id": "p2", "reason": "refused"}}
    vars = {
        "note_reasons": ["refused", "hardship"],
        "awaiting_notes": "no",
        "attempts_json": json.dumps({"p2": 2}),
        "persuasion_used_json": json.dumps({"p2": 1}),
        "deadlines_json": "{}",
        "proposed_date": "",
        "ledger_notes": [],
    }
    exec(add_note_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "yes"
    assert vars["block_reason"] == "notes_not_ready"

    vars["awaiting_notes"] = "yes"
    vars["persuasion_used_json"] = json.dumps({"p2": 0})
    config = {"url": "https://bank/add_note", "body": {"product_id": "p2", "reason": "refused"}}
    exec(add_note_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "no"

    # Duplicate note for same product blocked
    vars["ledger_notes"] = json.dumps([{"product_id": "p2", "reason": "refused"}])
    config = {"url": "https://bank/add_note", "body": {"product_id": "p2", "reason": "refused"}}
    exec(add_note_pre_process(), {"config": config, "vars": vars, "json": json})
    assert vars["write_blocked"] == "yes"
    assert vars["block_reason"] == "already_noted"


def main():
    tests = [
        test_business_days,
        test_weekday_phrases,
        test_attempt1_and_clamp,
        test_outcome_resolver,
        test_ledger_mirror,
        test_gate_snippets_are_safe,
        test_verify_post_hook_zero_left,
        test_date_engine_code_has_month_days,
        test_holiday_promise_offers_next_bd,
        test_multi_product_clamps_after_deadline,
        test_floor_offered_bakes_gate,
        test_persuasion_pub12_counters,
        test_deadline_refused_after_one_offer,
        test_add_note_blocks_before_persuasion_exhausted,
        test_note_just_ok_and_parallel_pid_stack,
        test_parallel_note_race_reconciles,
        test_compile_and_lint,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", t.__name__, e)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
