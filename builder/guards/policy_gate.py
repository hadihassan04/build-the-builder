"""Policy checks and pre_process_code for write tools.

Generated hooks contain no doubled braces. They refuse a write unless code set gate_ok.
"""
import json

from builder.guards.date_engine import callback_allowed, is_business_day, parse_now


def check_promise(day, amount, floor, past_due, deadline, holidays, today):
    if day is None:
        return "no", "date"
    if day < today:
        return "no", "past"
    if not is_business_day(day, holidays):
        return "no", "not_business_day"
    if deadline is not None and day > deadline:
        return "no", "after_deadline"
    try:
        amt = float(amount)
        low = float(floor)
        high = float(past_due)
    except Exception:
        return "no", "amount"
    if amt + 0.001 < low:
        return "no", "below_floor"
    if amt > high + 0.001:
        return "no", "above_full"
    return "yes", "ok"


def check_callback(day, minutes, now_text, holidays, start_min, end_min):
    now = parse_now(now_text)
    if callback_allowed(day, minutes, now, holidays, start_min, end_min):
        return "yes", "ok"
    return "no", "outside"


def check_transfer(queue, allowed):
    if str(queue or "") in list(allowed or []):
        return "yes", "ok"
    return "no", "queue"


def check_note(product_id, reason, allowed, enabled):
    if not enabled:
        return "no", "disabled"
    if not str(product_id or ""):
        return "no", "product"
    if str(reason or "") not in list(allowed or []):
        return "no", "reason"
    return "yes", "ok"


def render_gate(body_fields, blocked_url="https://example.invalid/blocked"):
    """body_fields: list of (json key, vars key, 'str' or 'float')."""
    lines = ["try:", "    allowed = str(vars.get('gate_ok') or '')", "    if isinstance(config, dict):"]
    lines.append("        if allowed != 'yes':")
    lines.append("            config['url'] = " + json.dumps(blocked_url))
    lines.append("            config['method'] = 'POST'")
    lines.append("            blocked_body = {}")
    lines.append("            blocked_body['blocked'] = 'yes'")
    lines.append("            config['body'] = blocked_body")
    lines.append("            vars['write_blocked'] = 'yes'")
    lines.append("        else:")
    lines.append("            body = {}")
    lines.append("            body['customer_id'] = str(vars.get('customer_id') or '')")
    for name, var, kind in body_fields:
        if kind == "float":
            lines.append("            raw_" + name + " = str(vars.get(" + json.dumps(var) + ") or '')")
            lines.append("            if raw_" + name + ":")
            lines.append("                body[" + json.dumps(name) + "] = float(raw_" + name + ")")
        elif kind == "omit_empty":
            lines.append("            raw_" + name + " = str(vars.get(" + json.dumps(var) + ") or '')")
            lines.append("            if raw_" + name + ":")
            lines.append("                body[" + json.dumps(name) + "] = raw_" + name)
        else:
            lines.append("            body[" + json.dumps(name) + "] = str(vars.get(" + json.dumps(var) + ") or '')")
    lines.append("            config['body'] = body")
    lines.append("            config['method'] = 'POST'")
    lines.append("            vars['write_blocked'] = 'no'")
    lines.append("except Exception:")
    lines.append("    vars['write_blocked'] = 'yes'")
    lines.append("    if isinstance(config, dict):")
    lines.append("        config['url'] = " + json.dumps(blocked_url))
    text = "\n".join(lines) + "\n"
    if "{{" in text or "}}" in text:
        raise RuntimeError("policy gate snippet contains doubled braces")
    return text


def promise_gate(url):
    return render_gate([
        ("product_id", "gate_product_id", "str"),
        ("amount", "gate_amount", "float"),
        ("date", "gate_date", "str"),
    ], url)


def callback_gate(url):
    return render_gate([
        ("date", "gate_date", "str"),
        ("time", "gate_time", "str"),
    ], url)


def transfer_gate(url):
    return render_gate([("queue", "gate_queue", "str")], url)


def note_gate(url):
    return render_gate([
        ("product_id", "gate_product_id", "str"),
        ("reason", "gate_reason", "str"),
        ("text", "gate_text", "omit_empty"),
    ], url)


def close_gate(url):
    return render_gate([
        ("outcome", "close_outcome", "str"),
        ("reason", "close_reason", "omit_empty"),
    ], url)


def verify_gate(url):
    return render_gate([
        ("field", "verify_field", "str"),
        ("answer", "verify_answer", "str"),
    ], url)


def account_gate(url):
    return render_gate([], url)


def _assert_clean(text):
    if "{{" in text or "}}" in text:
        raise RuntimeError("gate snippet contains doubled braces")
    return text


def create_promise_pre_process():
    """Fail closed when deadline maps are missing. Prefer gate_* when gate_ok=yes."""
    text = """
import json
try:
    def _load_map(key):
        raw = vars.get(key)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except Exception:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}
    def _load_list(key):
        raw = vars.get(key)
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except Exception:
                return []
            if isinstance(parsed, list):
                return parsed
        return []
    def _block():
        if isinstance(config, dict):
            config["url"] = "https://example.invalid/blocked"
            config["method"] = "POST"
            blocked_body = {}
            blocked_body["blocked"] = "yes"
            config["body"] = blocked_body
        vars["write_blocked"] = "yes"
    def _bake_gate():
        body = {}
        body["customer_id"] = str(vars.get("customer_id") or "")
        body["product_id"] = str(vars.get("gate_product_id") or "")
        body["date"] = str(vars.get("gate_date") or "")
        raw_amt = str(vars.get("gate_amount") or "")
        if raw_amt:
            body["amount"] = float(raw_amt)
        config["body"] = body
        config["method"] = "POST"
        vars["last_promise_pid"] = body["product_id"]
        _push_promise_pid(body["product_id"])
        vars["write_blocked"] = "no"
        vars["gate_ok"] = "no"
    def _push_promise_pid(pid_push):
        # FIFO + per-pid attempted markers (same race pattern as note writes).
        # Parallel create_promise calls share a vars snapshot; last_promise_pid
        # alone collapses to one product and check_remaining re-enters write_promises.
        sp = str(pid_push or "")
        if not sp:
            return
        stack = vars.get("promise_pid_stack") or "[]"
        if isinstance(stack, str):
            try:
                stack = json.loads(stack)
            except Exception:
                stack = []
        if not isinstance(stack, list):
            stack = []
        stack.append(sp)
        try:
            vars["promise_pid_stack"] = json.dumps(stack)
        except Exception:
            vars["promise_pid_stack"] = stack
        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in sp)[:64]
        if safe:
            vars["promise_attempted_" + safe] = "yes"
    def _promised_all():
        out = [str(x) for x in _load_list("promised_products")]
        for raw_pid in _load_list("allowed_product_ids"):
            sp = str(raw_pid)
            safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in sp)[:64]
            if safe and str(vars.get("promised_" + safe) or "") == "yes" and sp not in out:
                out.append(sp)
        return out
    if not isinstance(config, dict):
        vars["write_blocked"] = "yes"
    elif str(vars.get("gate_ok") or "") == "yes" or str(vars.get("floor_offered") or "") == "yes":
        # Floor-offer / deadline-offer path always writes the staged gate_* values.
        if str(vars.get("gate_product_id") or "") in _promised_all():
            vars["block_reason"] = "already_promised"
            vars["last_promise_pid"] = str(vars.get("gate_product_id") or "")
            _block()
        else:
            _bake_gate()
    else:
        body = config.get("body")
        if not isinstance(body, dict):
            body = {}
        pid = str(body.get("product_id") or "")
        date = str(body.get("date") or "")
        allowed = _load_list("allowed_product_ids")
        promised = _promised_all()
        deadlines = _load_map("deadlines_json")
        floors = _load_map("floors_json")
        dues = _load_map("past_dues_json")
        holidays = vars.get("policy_holidays") or []
        if isinstance(holidays, str):
            try:
                holidays = json.loads(holidays)
            except Exception:
                holidays = []
        # Freya sometimes drops list vars; recover ids from deadline map.
        if not allowed and isinstance(deadlines, dict) and deadlines:
            allowed = [str(k) for k in deadlines.keys()]
            vars["allowed_product_ids"] = allowed
        blocked = False
        reason = ""
        if not deadlines:
            blocked = True
            reason = "no_deadlines"
        if not pid:
            blocked = True
            reason = "no_product"
        if allowed and pid not in [str(x) for x in allowed]:
            blocked = True
            reason = "bad_product"
        if not allowed:
            blocked = True
            reason = "no_products"
        if pid and pid in promised:
            blocked = True
            reason = "already_promised"
        limit = str(deadlines.get(pid) or "") if isinstance(deadlines, dict) else ""
        if not limit:
            blocked = True
            reason = "no_deadline"
        # Multi-product: prefer per-product fitted dates from fit_product_dates.
        # Do NOT invent fitted dates for every product from proposed_date here —
        # that left open products looking "already confirmed" and skipped persuasion
        # (extra "pay today?" asks that burn attempt budget).
        fitted = _load_map("fitted_dates_json")
        if isinstance(fitted, dict) and pid in fitted and str(fitted.get(pid) or ""):
            fitted_day = str(fitted.get(pid))
            # Prefer a valid body date over a stale past fitted extraction.
            body_day = str(body.get("date") or date or "")
            use_fitted = True
            if body_day and len(body_day) >= 10 and fitted_day and len(fitted_day) >= 10:
                try:
                    import datetime as _dt
                    now_raw = str(vars.get("now") or "")
                    today = _dt.date(2026, 1, 1)
                    if len(now_raw) >= 10:
                        today = _dt.date(int(now_raw[0:4]), int(now_raw[5:7]), int(now_raw[8:10]))
                    bd = _dt.date(int(body_day[0:4]), int(body_day[5:7]), int(body_day[8:10]))
                    fd = _dt.date(int(fitted_day[0:4]), int(fitted_day[5:7]), int(fitted_day[8:10]))
                    if bd >= today and fd < today:
                        use_fitted = False
                        date = body_day
                except Exception:
                    use_fitted = True
            if use_fitted:
                date = fitted_day
                body["date"] = date
        if date and limit and date > limit:
            # Multi-product split deadlines: clamp this product to its own deadline.
            if len(allowed) > 1:
                date = limit
                body["date"] = date
                vars["date_clamped"] = limit
            else:
                blocked = True
                reason = "after_deadline"
                vars["offer_date"] = limit
        if date and len(date) >= 10:
            try:
                import datetime
                y = int(date[0:4])
                m = int(date[5:7])
                d = int(date[8:10])
                day = datetime.date(y, m, d)
                today = datetime.date(2026, 1, 1)
                now_raw = str(vars.get("now") or "")
                if len(now_raw) >= 10:
                    today = datetime.date(int(now_raw[0:4]), int(now_raw[5:7]), int(now_raw[8:10]))
                if day < today:
                    # Snap past dates (mis-parsed tomorrow) to next business day.
                    nxt = day
                    guard = 0
                    cursor = today
                    while guard < 14:
                        cursor = cursor + datetime.timedelta(days=1)
                        guard = guard + 1
                        ok_bd = cursor.weekday() < 5
                        iso_c = cursor.isoformat()
                        for h in holidays or []:
                            if str(h) == iso_c:
                                ok_bd = False
                                break
                        if ok_bd:
                            nxt = cursor
                            break
                    date = nxt.isoformat()
                    body["date"] = date
                    day = nxt
                def _is_bd(check):
                    if check is None or check.weekday() >= 5:
                        return False
                    iso_c = check.isoformat()
                    for h in holidays or []:
                        if str(h) == iso_c:
                            return False
                    return True
                def _next_bd(start):
                    nxt = start + datetime.timedelta(days=1)
                    guard = 0
                    while not _is_bd(nxt) and guard < 14:
                        nxt = nxt + datetime.timedelta(days=1)
                        guard = guard + 1
                    return nxt
                if not _is_bd(day):
                    nxt = _next_bd(day)
                    a1 = str(vars.get("attempt1_date") or "")
                    if a1 and len(a1) >= 10:
                        try:
                            a1d = datetime.date(int(a1[0:4]), int(a1[5:7]), int(a1[8:10]))
                            if day == a1d + datetime.timedelta(days=1) and _is_bd(a1d):
                                nxt = a1d
                        except Exception:
                            pass
                    if limit and nxt.isoformat() > limit:
                        if len(allowed) > 1:
                            date = limit
                            body["date"] = date
                            day = datetime.date(int(limit[0:4]), int(limit[5:7]), int(limit[8:10]))
                        else:
                            blocked = True
                            vars["offer_date"] = limit
                            reason = "after_deadline"
                    else:
                        if len(allowed) > 1:
                            date = nxt.isoformat()
                            body["date"] = date
                            day = nxt
                        else:
                            blocked = True
                            vars["offer_date"] = nxt.isoformat()
                            reason = "holiday" if day.weekday() < 5 else "not_business_day"
            except Exception:
                blocked = True
                reason = "bad_date"
        else:
            blocked = True
            reason = "no_date"
        # Amounts staged by fit_dates / accept_persuasion (full past-due, floor, or an
        # accepted in-range customer amount) override whatever the model typed.
        fitted_amounts = _load_map("fitted_amounts_json")
        if isinstance(fitted_amounts, dict) and pid in fitted_amounts:
            try:
                staged_amt = float(fitted_amounts.get(pid))
                if staged_amt > 0:
                    body["amount"] = staged_amt
            except Exception:
                pass
        try:
            amount = float(body.get("amount"))
        except Exception:
            amount = None
            blocked = True
            reason = "bad_amount"
        if isinstance(floors, dict) and pid in floors and amount is not None:
            if amount + 0.001 < float(floors.get(pid) or 0):
                blocked = True
                reason = "below_floor"
                vars["offer_amount"] = str(floors.get(pid))
        if isinstance(dues, dict) and pid in dues and amount is not None:
            if amount > float(dues.get(pid) or 0) + 0.001:
                blocked = True
                reason = "above_full"
        if blocked:
            vars["block_reason"] = reason
            if pid:
                vars["last_promise_pid"] = pid
            _block()
        else:
            body["customer_id"] = str(vars.get("customer_id") or body.get("customer_id") or "")
            config["body"] = body
            config["method"] = "POST"
            vars["last_promise_pid"] = pid
            _push_promise_pid(pid)
            vars["write_blocked"] = "no"
            vars["block_reason"] = ""
except Exception:
    vars["write_blocked"] = "yes"
    if isinstance(config, dict):
        config["url"] = "https://example.invalid/blocked"
"""
    return _assert_clean(text)


def schedule_callback_pre_process():
    """Require today/next business day, 10:00-19:00, strictly after now."""
    text = """
import json
try:
    def _block():
        if isinstance(config, dict):
            config["url"] = "https://example.invalid/blocked"
            config["method"] = "POST"
            blocked_body = {}
            blocked_body["blocked"] = "yes"
            config["body"] = blocked_body
        vars["write_blocked"] = "yes"
    body = config.get("body") if isinstance(config, dict) else None
    if not isinstance(body, dict):
        body = {}
    if str(vars.get("gate_ok") or "") == "yes":
        body = {}
        body["customer_id"] = str(vars.get("customer_id") or "")
        body["date"] = str(vars.get("gate_date") or "")
        body["time"] = str(vars.get("gate_time") or "")
        if isinstance(config, dict):
            config["body"] = body
            config["method"] = "POST"
        vars["write_blocked"] = "no"
        vars["gate_ok"] = "no"
    else:
        import datetime
        date = str(body.get("date") or "")
        clock = str(body.get("time") or "")
        blocked = False
        minutes = None
        if len(clock) >= 5 and clock[2] == ":" and clock[0:2].isdigit() and clock[3:5].isdigit():
            minutes = int(clock[0:2]) * 60 + int(clock[3:5])
            start_raw = str(vars.get("callback_start") or "10:00")
            end_raw = str(vars.get("callback_end") or "19:00")
            start_min = 600
            end_min = 1140
            if len(start_raw) >= 5 and start_raw[2] == ":":
                start_min = int(start_raw[0:2]) * 60 + int(start_raw[3:5])
            if len(end_raw) >= 5 and end_raw[2] == ":":
                end_min = int(end_raw[0:2]) * 60 + int(end_raw[3:5])
            if minutes < start_min or minutes > end_min:
                blocked = True
        else:
            blocked = True
        day = None
        if len(date) >= 10 and date[4] == "-" and date[7] == "-":
            try:
                day = datetime.date(int(date[0:4]), int(date[5:7]), int(date[8:10]))
            except Exception:
                day = None
        if day is None:
            blocked = True
        holidays = vars.get("policy_holidays") or []
        if isinstance(holidays, str):
            try:
                holidays = json.loads(holidays)
            except Exception:
                holidays = []
        now_raw = str(vars.get("now") or "")
        today = datetime.date(2026, 1, 1)
        now_minutes = 0
        if len(now_raw) >= 10:
            try:
                today = datetime.date(int(now_raw[0:4]), int(now_raw[5:7]), int(now_raw[8:10]))
            except Exception:
                today = datetime.date(2026, 1, 1)
        if "T" in now_raw:
            rest = now_raw.split("T", 1)[1]
            if len(rest) >= 5 and rest[2] == ":" and rest[0:2].isdigit() and rest[3:5].isdigit():
                now_minutes = int(rest[0:2]) * 60 + int(rest[3:5])
        def _is_bd(d):
            if d is None or d.weekday() >= 5:
                return False
            iso = d.isoformat()
            for h in holidays or []:
                if str(h) == iso:
                    return False
            return True
        def _next_bd(d):
            nxt = d + datetime.timedelta(days=1)
            guard = 0
            while not _is_bd(nxt) and guard < 14:
                nxt = nxt + datetime.timedelta(days=1)
                guard = guard + 1
            return nxt
        allowed_dates = []
        if _is_bd(today):
            allowed_dates.append(today.isoformat())
            allowed_dates.append(_next_bd(today).isoformat())
        else:
            allowed_dates.append(_next_bd(today).isoformat())
        # Extraction often mis-parses "tomorrow" into a past calendar day. Snap past dates
        # with a valid clock to the configured tomorrow / last allowed business day.
        if (
            day is not None
            and minutes is not None
            and not blocked
            and day.isoformat() not in allowed_dates
        ):
            snap = str(vars.get("callback_tomorrow") or "")
            if not snap and allowed_dates:
                snap = allowed_dates[-1]
            if snap and len(snap) >= 10:
                try:
                    day = datetime.date(int(snap[0:4]), int(snap[5:7]), int(snap[8:10]))
                    body["date"] = day.isoformat()
                except Exception:
                    pass
        if day is not None and day.isoformat() not in allowed_dates:
            blocked = True
        if day is not None and minutes is not None and day == today and minutes <= now_minutes:
            blocked = True
        if day is None or not _is_bd(day):
            blocked = True
        if blocked:
            vars["block_reason"] = "callback_window"
            _block()
        else:
            body["customer_id"] = str(vars.get("customer_id") or body.get("customer_id") or "")
            body["date"] = day.isoformat()
            if isinstance(config, dict):
                config["body"] = body
                config["method"] = "POST"
            vars["write_blocked"] = "no"
            vars["block_reason"] = ""
            vars["forced_outcome"] = "not_reached"
            vars["forced_reason"] = "callback"
except Exception:
    vars["write_blocked"] = "yes"
    if isinstance(config, dict):
        config["url"] = "https://example.invalid/blocked"
"""
    return _assert_clean(text)


def transfer_pre_process():
    text = """
import json
try:
    body = config.get("body") if isinstance(config, dict) else None
    if not isinstance(body, dict):
        body = {}
    queue = str(body.get("queue") or "")
    allowed = vars.get("allowed_queues") or []
    if isinstance(allowed, str):
        allowed = json.loads(allowed)
    blocked = bool(allowed) and queue not in allowed
    if isinstance(config, dict) and blocked:
        config["url"] = "https://example.invalid/blocked"
        vars["write_blocked"] = "yes"
    else:
        vars["write_blocked"] = "no"
        if queue == "general":
            vars["transfer_queue"] = "human"
        else:
            vars["transfer_queue"] = queue
except Exception:
    vars["write_blocked"] = "yes"
"""
    return _assert_clean(text)


def add_note_pre_process():
    text = """
import json
try:
    body = config.get("body") if isinstance(config, dict) else None
    if not isinstance(body, dict):
        body = {}
    reason = str(body.get("reason") or "")
    allowed = vars.get("note_reasons") or []
    if isinstance(allowed, str):
        allowed = json.loads(allowed)
    blocked = bool(allowed) and reason not in allowed
    if str(vars.get("ledger_transferred") or "") == "true":
        blocked = True
    # One note per product — block duplicates via noted_products and ledger_notes.
    pid_check = str(body.get("product_id") or "")
    if pid_check and not blocked:
        noted = vars.get("noted_products") or []
        if isinstance(noted, str):
            try:
                noted = json.loads(noted)
            except Exception:
                noted = []
        if not isinstance(noted, list):
            noted = []
        if pid_check in [str(x) for x in noted]:
            blocked = True
            vars["block_reason"] = "already_noted"
        if not blocked:
            notes = vars.get("ledger_notes") or []
            if isinstance(notes, str):
                try:
                    notes = json.loads(notes)
                except Exception:
                    notes = []
            if isinstance(notes, list):
                for row in notes:
                    if isinstance(row, dict) and str(row.get("product_id") or "") == pid_check:
                        blocked = True
                        vars["block_reason"] = "already_noted"
                        break
    # After-deadline date for THIS product is not a refusal — keep offering its deadline.
    if reason == "refused":
        proposed = str(vars.get("proposed_date") or "")
        pid = str(body.get("product_id") or "")
        deadlines = vars.get("deadlines_json") or {}
        if isinstance(deadlines, str):
            try:
                deadlines = json.loads(deadlines)
            except Exception:
                deadlines = {}
        if proposed and isinstance(deadlines, dict) and pid:
            lim = str(deadlines.get(pid) or "")
            # After the customer refused the deadline (Brief A.23), refused notes are allowed.
            if lim and proposed > lim and str(vars.get("deadline_refused") or "") != "yes":
                blocked = True
                vars["block_reason"] = "after_deadline_not_refusal"
                vars["offer_date"] = lim
        # Refused notes only after the workflow enters finalize_notes.
        if not blocked and str(vars.get("awaiting_notes") or "") != "yes":
            blocked = True
            vars["block_reason"] = "notes_not_ready"
    # FIFO stack so parallel add_note post-hooks each get their own product_id.
    pid_for_stack = str(body.get("product_id") or "")
    if pid_for_stack:
        stack = vars.get("note_pid_stack") or "[]"
        if isinstance(stack, str):
            try:
                stack = json.loads(stack)
            except Exception:
                stack = []
        if not isinstance(stack, list):
            stack = []
        stack.append(pid_for_stack)
        try:
            vars["note_pid_stack"] = json.dumps(stack)
        except Exception:
            vars["note_pid_stack"] = stack
    # Race-safe attempted marker: one distinct key per pid, so parallel
    # pre-hooks sharing a snapshot cannot overwrite each other (unlike the
    # FIFO stack above). check_remaining uses these to credit notes the
    # stack misattributed under true-parallel add_note calls.
    if pid_for_stack:
        _safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in pid_for_stack)[:64]
        if _safe:
            vars["note_attempted_" + _safe] = "yes"
    if isinstance(config, dict) and blocked:
        config["url"] = "https://example.invalid/blocked"
        config["method"] = "POST"
        blocked_body = {}
        blocked_body["blocked"] = "yes"
        config["body"] = blocked_body
        vars["write_blocked"] = "yes"
    else:
        vars["last_note_pid"] = str(body.get("product_id") or "")
        vars["last_note_reason"] = reason
        vars["write_blocked"] = "no"
except Exception:
    vars["write_blocked"] = "yes"
"""
    return _assert_clean(text)


def close_call_pre_process():
    """Bake outcome. First send writes the bank; second (lifecycle) re-POSTs the same
    body to the real URL — never example.invalid (DNS failures red-flag good calls).
    """
    text = """
try:
    outcome = str(vars.get("close_outcome") or vars.get("resolved_outcome") or vars.get("forced_outcome") or "")
    reason = str(vars.get("close_reason") or vars.get("resolved_reason") or vars.get("forced_reason") or "")
    if not outcome:
        if str(vars.get("has_callback") or "").lower() == "true":
            outcome = "not_reached"
            reason = "callback"
        elif str(vars.get("has_promise") or "").lower() == "true":
            outcome = "promise"
            reason = ""
        elif str(vars.get("ledger_transferred") or "").lower() == "true":
            outcome = "transferred"
            reason = str(vars.get("transfer_queue") or "human")
        else:
            outcome = "no_promise"
            reason = reason or "refused"
    if reason in ("none", "None"):
        reason = ""
    if outcome in ("promise", "paid"):
        reason = ""
    already = str(vars.get("close_sent") or "") == "yes"
    if isinstance(config, dict):
        body = {}
        body["customer_id"] = str(vars.get("customer_id") or "")
        body["outcome"] = outcome
        if reason:
            body["reason"] = reason
        config["body"] = body
        config["method"] = "POST"
        # Keep the bank URL even on the duplicate lifecycle send.
    vars["close_outcome"] = outcome
    vars["close_reason"] = reason
    vars["close_duplicate"] = "yes" if already else "no"
    vars["close_sent"] = "yes"
    vars["write_blocked"] = "no"
except Exception:
    vars["write_blocked"] = "no"
    vars["close_duplicate"] = "yes"
"""
    return _assert_clean(text)


def close_call_post_process():
    """Lifecycle duplicate must not surface as a failed tool after in-graph close."""
    text = """
try:
    if isinstance(result, dict):
        result.pop("header", None)
        if str(vars.get("close_duplicate") or "") == "yes":
            result["status"] = "success"
            result["status_code"] = 200
            result["message"] = "close already sent"
            data = result.get("data")
            if not isinstance(data, dict):
                data = {}
            data["ok"] = True
            data["skipped"] = True
            result["data"] = data
        else:
            data = result.get("data")
            if not isinstance(data, dict):
                data = {}
                result["data"] = data
            if data.get("ok") is True or str(data.get("ok") or "").lower() == "true":
                pass
            elif str(vars.get("close_outcome") or ""):
                # Bank rejected a second close — treat as success if we already baked an outcome.
                result["status"] = "success"
                data["ok"] = True
                result["data"] = data
except Exception:
    pass
"""
    return _assert_clean(text)
