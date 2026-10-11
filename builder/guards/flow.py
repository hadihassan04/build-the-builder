"""Call state machine. Embedded into Freya code nodes together with the other guards."""
import json

try:
    from builder.guards.date_engine import (
        add_business_days, attempt_date, band_tuple, callback_allowed, clock_text,
        fit_named_date, parse_clock, parse_dob, parse_iso_date, parse_now, parse_when, speak_date,
    )
    from builder.guards.ledger_mirror import add_item, has_product, load_json, save_json
    from builder.guards.outcome_resolver import resolve
except Exception:
    pass

CTX = {}


def _list(value):
    if isinstance(value, list):
        return value
    return []


def configure(policy):
    CTX["policy"] = policy
    calendar = policy.get("calendar") or {}
    holidays = calendar.get("holidays") or {}
    CTX["holidays"] = _list(holidays.get("dates"))
    bands = _list(policy.get("bands"))
    CTX["band_min"] = [row.get("min_days") for row in bands]
    CTX["band_max"] = [row.get("max_days") for row in bands]
    CTX["band_bd"] = [row.get("deadline_business_days") for row in bands]
    CTX["band_attempts"] = [row.get("attempts") for row in bands]
    CTX["band_name"] = [row.get("name") for row in bands]
    amounts = policy.get("amounts") or {}
    floors = _list(amounts.get("floors"))
    CTX["floor_type"] = [row.get("product_type") for row in floors]
    CTX["floor_field"] = [row.get("field") for row in floors]
    products = policy.get("products") or {}
    labels = _list(products.get("labels"))
    CTX["label_type"] = [row.get("type") for row in labels]
    CTX["label_text"] = [row.get("label") for row in labels]
    CTX["label_last4"] = ["yes" if row.get("last4") else "no" for row in labels]
    CTX["intro"] = str(products.get("intro") or "")
    CTX["sentence"] = str(products.get("sentence") or "")
    identity = policy.get("identity") or {}
    questions = _list(identity.get("questions"))
    questions = sorted(questions, key=lambda row: int(row.get("order") or 0))
    CTX["q_field"] = [row.get("field") for row in questions]
    CTX["q_prompt"] = [row.get("prompt") for row in questions]
    CTX["max_wrong"] = int(identity.get("max_wrong") or 3)
    CTX["hint_prompt"] = str(identity.get("hint_prompt") or "")
    CTX["fail_sentence"] = str(identity.get("fail_sentence") or "")
    CTX["decline_explain"] = str(identity.get("decline_explain") or "")
    CTX["wrong_prompt"] = str(identity.get("wrong_prompt") or "")
    opening = policy.get("opening") or {}
    CTX["ask_template"] = str(opening.get("ask_template") or "")
    CTX["ai_sentence"] = str(opening.get("ai_sentence") or "")
    CTX["scam_sentence"] = str(opening.get("scam_sentence") or "")
    CTX["purpose_sentence"] = str(opening.get("purpose_sentence") or "")
    CTX["manip_sentence"] = str(opening.get("manipulation_sentence") or "")
    third = policy.get("third_party") or {}
    CTX["third_ask"] = str(third.get("ask") or "")
    CTX["deceased_sentence"] = str(third.get("deceased_sentence") or "")
    callback = third.get("callback") or {}
    CTX["window_start"] = _minutes(callback.get("start") or "10:00")
    CTX["window_end"] = _minutes(callback.get("end") or "19:00")
    negotiation = policy.get("negotiation") or {}
    CTX["today_one"] = str(negotiation.get("today_ask_one") or "")
    CTX["today_all"] = str(negotiation.get("today_ask_all") or "")
    CTX["vague"] = _list(negotiation.get("vague_phrases"))
    CTX["sympathy"] = str(negotiation.get("sympathy") or "")
    CTX["support_offer"] = str(negotiation.get("support_offer") or "")
    CTX["floor_rest"] = str(negotiation.get("floor_rest") or "")
    CTX["below_floor"] = str(negotiation.get("cannot_record_below") or "")
    CTX["latest_date"] = str(negotiation.get("latest_date") or "")
    CTX["next_bd_line"] = str(negotiation.get("next_business_offer") or "")
    CTX["exact_day"] = str(negotiation.get("ask_exact_day") or "What exact day works?")
    CTX["help_first"] = str(negotiation.get("help_first") or "I can help with this call. Would you like to continue with me?")
    confirm = policy.get("confirm") or {}
    CTX["confirm_ask"] = str(confirm.get("ask") or "Do you confirm?")
    CTX["sms"] = str(confirm.get("sms") or "")
    CTX["recorded"] = str(confirm.get("recorded") or "")
    CTX["could_not"] = str(confirm.get("could_not") or "")
    CTX["app_change"] = str(confirm.get("app_change") or "")
    CTX["paid_delay"] = str(confirm.get("paid_delay") or "")
    notes = policy.get("notes") or {}
    behaviour = policy.get("behaviour") or {}
    enabled = notes.get("enabled")
    if enabled is None:
        enabled = behaviour.get("notes_enabled")
    CTX["notes"] = "yes" if enabled else "no"
    CTX["dispute_continues"] = "yes" if behaviour.get("dispute_continues") else "no"
    CTX["paid_continues"] = "yes" if behaviour.get("paid_continues") else "no"
    CTX["note_reasons"] = _list(notes.get("reasons"))
    detectors = policy.get("detectors") or {}
    for key in (
        "confirm", "not_confirm", "deceased", "wrong_number", "other_person", "ai", "scam",
        "hardship", "dispute", "paid", "restructuring", "human", "stop", "manipulation", "yes", "decline",
    ):
        CTX[key] = [str(item).lower() for item in _list(detectors.get(key))]
    queues = policy.get("queues") or {}
    CTX["queue_support"] = str(queues.get("support") or "support")
    CTX["queue_restructuring"] = str(queues.get("restructuring") or "restructuring")
    CTX["queue_human"] = str(queues.get("human") or "general")
    CTX["reason_human"] = str(queues.get("human_reason") or "human")


def _minutes(text):
    raw = str(text or "0:0")
    hour, minute = raw.split(":", 1)
    return int(hour) * 60 + int(minute)


def _fill(template, pairs):
    out = str(template or "")
    for key, value in pairs:
        out = out.replace("{" + key + "}", str(value))
    return out


def _has(text, key):
    raw = " " + " ".join(str(text or "").lower().split()) + " "
    for phrase in CTX.get(key) or []:
        if phrase and (" " + phrase + " ") in raw:
            return True
    return False


def _name_in(text, vars):
    raw = str(text or "").lower()
    last = str(vars.get("last_name") or "").lower()
    first = str(vars.get("first_name") or "").lower()
    if last and last in raw:
        return True
    if first and last and (first + " " + last) in raw:
        return True
    return False


def _products(vars):
    return load_json(vars, "products_json")


def _save_products(vars, rows):
    save_json(vars, "products_json", rows)


def _today(vars):
    return parse_now(str(vars.get("now") or "")).date()


def _now(vars):
    return parse_now(str(vars.get("now") or ""))


def _holidays():
    return CTX.get("holidays") or []


def _reask(vars):
    return _fill(CTX.get("ask_template"), [("title", vars.get("title") or ""), ("last_name", vars.get("last_name") or "")])


def _money(value):
    return "%.2f" % float(value)


def _truthy(value):
    return str(value).lower() == "true"


def _pending(vars, *names):
    for name in names:
        vars[name] = "pending"


def _listen_clear(vars):
    _pending(vars, "who_enum", "tp_enum", "v_enum", "n_enum", "c_enum")
    vars["utterance"] = ""
    vars["v_text"] = ""
    vars["n_date"] = ""
    vars["n_amount"] = ""
    vars["n_product"] = ""
    vars["c_date"] = ""
    vars["c_amount"] = ""


def run_boot(vars):
    vars["op"] = "boot"
    vars["phase"] = "opening"
    vars["ask_count"] = "1"
    vars["verified"] = "no"
    vars["verify_stage_i"] = "0"
    vars["decline_count"] = "0"
    vars["human_count"] = "0"
    vars["stop_count"] = "0"
    vars["window_asks"] = "0"
    vars["support_mode"] = "no"
    vars["neg_mode"] = "ask_today"
    vars["account_tries"] = "0"
    vars["promise_tries"] = "0"
    vars["note_tries"] = "0"
    vars["transfer_tries"] = "0"
    vars["callback_tries"] = "0"
    vars["close_outcome"] = ""
    vars["close_reason"] = ""
    vars["extra_line"] = ""
    vars["gate_ok"] = "no"
    vars["instruction"] = ""
    vars["last_ask"] = ""
    vars["active_product"] = ""
    for key in ("products_json", "promises_json", "notes_json", "transfers_json", "callbacks_json"):
        vars[key] = "[]"
    vars["route"] = "opening"
    _listen_clear(vars)


def _close(vars, outcome, reason, extra=""):
    vars["close_outcome"] = outcome
    vars["close_reason"] = reason
    vars["extra_line"] = extra
    vars["phase"] = "close"
    vars["route"] = "close"


def run_opening(vars):
    vars["op"] = "opening"
    utter = str(vars.get("utterance") or "")
    enum = str(vars.get("who_enum") or "")
    if _has(utter, "manipulation"):
        enum = "manipulation"
    elif _has(utter, "deceased"):
        enum = "deceased"
    elif _has(utter, "wrong_number"):
        enum = "wrong_number"
    elif _has(utter, "other_person"):
        enum = "other_person"
    elif _has(utter, "ai"):
        enum = "ai_question"
    elif _has(utter, "scam"):
        enum = "scam_question"
    elif _has(utter, "stop"):
        enum = "stop"
    elif is_confirmed(utter, vars, enum):
        enum = "confirmed"
    elif _has(utter, "not_confirm"):
        enum = "unclear"
    elif enum not in ("confirmed", "unclear", "other_person", "wrong_number", "deceased", "ai_question", "scam_question", "question", "manipulation", "stop"):
        enum = "unclear"
    if enum == "confirmed":
        vars["phase"] = "verify"
        vars["verify_stage_i"] = "0"
        vars["say_line"] = str((CTX.get("q_prompt") or [""])[0])
        vars["last_ask"] = vars["say_line"]
        vars["route"] = "verify"
    elif enum == "deceased":
        _close(vars, "not_reached", "deceased", CTX.get("deceased_sentence") or "")
    elif enum == "wrong_number":
        _close(vars, "not_reached", "wrong_number")
    elif enum == "other_person":
        vars["phase"] = "third"
        vars["say_line"] = CTX.get("third_ask") or ""
        vars["last_ask"] = vars["say_line"]
        vars["route"] = "third"
    elif enum == "stop":
        count = int(vars.get("stop_count") or "0") + 1
        vars["stop_count"] = str(count)
        if count >= 2:
            _close(vars, "not_verified", "declined")
        else:
            vars["say_line"] = _reask(vars)
            vars["route"] = "reask"
    elif enum in ("ai_question", "scam_question", "question", "manipulation"):
        vars["ask_count"] = "2"
        if enum == "ai_question":
            vars["route"] = "ai"
        elif enum == "scam_question":
            vars["route"] = "scam"
        elif enum == "manipulation":
            vars["say_line"] = (CTX.get("manip_sentence") or "") + " " + _reask(vars)
            vars["route"] = "reask"
        else:
            vars["say_line"] = (CTX.get("purpose_sentence") or "") + " " + _reask(vars)
            vars["route"] = "reask"
    else:
        count = int(vars.get("ask_count") or "1")
        if count >= 2:
            _close(vars, "not_reached", "unclear")
        else:
            vars["ask_count"] = str(count + 1)
            vars["say_line"] = _reask(vars)
            vars["route"] = "reask"
    _listen_clear(vars)


def is_confirmed(utter, vars, enum):
    if _has(utter, "confirm") or _name_in(utter, vars):
        if _has(utter, "not_confirm") and not _has(utter, "confirm") and not _name_in(utter, vars):
            return False
        return True
    if enum == "confirmed" and not str(utter or "").strip():
        return True
    if enum == "confirmed" and not _has(utter, "not_confirm"):
        return True
    return False


def run_third(vars):
    vars["op"] = "third"
    utter = str(vars.get("utterance") or "")
    enum = str(vars.get("tp_enum") or "")
    if _has(utter, "deceased") or enum == "deceased":
        _close(vars, "not_reached", "deceased", CTX.get("deceased_sentence") or "")
        _listen_clear(vars)
        return
    if _has(utter, "wrong_number") or enum == "wrong_number":
        _close(vars, "not_reached", "wrong_number")
        _listen_clear(vars)
        return
    if _has(utter, "manipulation"):
        vars["say_line"] = (CTX.get("manip_sentence") or "") + " " + str(CTX.get("third_ask") or "")
        vars["route"] = "third"
        _listen_clear(vars)
        return
    blob = utter + " " + str(vars.get("cb_day") or "") + " " + str(vars.get("cb_time") or "")
    kind, named = parse_when(blob, _today(vars), CTX.get("vague") or [])
    minutes = parse_clock(blob)
    if kind == "exact" and minutes is not None and callback_allowed(named, minutes, _now(vars), _holidays(), CTX.get("window_start"), CTX.get("window_end")):
        vars["gate_ok"] = "yes"
        vars["gate_date"] = named.isoformat()
        vars["gate_time"] = clock_text(minutes)
        vars["instruction"] = "Do not say the callback is booked until the tool succeeds."
        vars["route"] = "callback_call"
        _listen_clear(vars)
        return
    asks = int(vars.get("window_asks") or "0")
    if asks >= 1:
        _close(vars, "not_reached", "no_callback")
    else:
        vars["window_asks"] = "1"
        vars["say_line"] = CTX.get("third_ask") or ""
        vars["route"] = "third"
    _listen_clear(vars)


def run_callback_tally(vars):
    vars["op"] = "callback_tally"
    if _truthy(vars.get("cb_ok")):
        add_item(vars, "callbacks_json", {"date": str(vars.get("gate_date") or ""), "time": str(vars.get("gate_time") or "")})
        _close(vars, "not_reached", "callback")
        return
    tries = int(vars.get("callback_tries") or "0")
    if tries < 1:
        vars["callback_tries"] = "1"
        vars["gate_ok"] = "yes"
        vars["route"] = "callback_call"
        vars["instruction"] = "The callback was not booked. Do not say it was booked."
        return
    _close(vars, "not_reached", "no_callback", CTX.get("could_not") or "")


def _question(vars):
    index = int(vars.get("verify_stage_i") or "0")
    prompts = CTX.get("q_prompt") or [""]
    if index < 0 or index >= len(prompts):
        return ""
    return str(prompts[index])


def run_verify_prep(vars):
    vars["op"] = "verify_prep"
    utter = str(vars.get("v_text") or vars.get("utterance") or "")
    enum = str(vars.get("v_enum") or "")
    if _has(utter, "manipulation"):
        vars["say_line"] = (CTX.get("manip_sentence") or "") + " " + _question(vars)
        vars["route"] = "verify"
        _listen_clear(vars)
        return
    if _has(utter, "stop") or enum == "stop":
        count = int(vars.get("stop_count") or "0") + 1
        vars["stop_count"] = str(count)
        if count >= 2:
            _close(vars, "not_verified", "declined")
        else:
            vars["say_line"] = _question(vars)
            vars["route"] = "verify"
        _listen_clear(vars)
        return
    if _has(utter, "human") or enum == "human":
        _human(vars, _question(vars))
        _listen_clear(vars)
        return
    declined = enum in ("decline", "asks_purpose") or _has(utter, "decline")
    if enum == "asks_purpose" or "what is this about" in utter.lower() or "why are you calling" in utter.lower():
        declined = True
    if declined and enum != "answer":
        count = int(vars.get("decline_count") or "0") + 1
        vars["decline_count"] = str(count)
        if count >= 2:
            _close(vars, "not_verified", "declined")
        else:
            vars["say_line"] = str(CTX.get("decline_explain") or "") + " " + _question(vars)
            vars["route"] = "verify"
        _listen_clear(vars)
        return
    fields = CTX.get("q_field") or []
    index = int(vars.get("verify_stage_i") or "0")
    field = str(fields[index]) if index < len(fields) else ""
    answer = utter.strip()
    if field == "date_of_birth":
        parsed = parse_dob(answer)
        if parsed is not None:
            answer = parsed.isoformat()
    elif field == "phone_last4":
        digits = "".join(ch for ch in answer if ch.isdigit())
        if len(digits) >= 4:
            answer = digits[-4:]
        else:
            vars["say_line"] = _question(vars)
            vars["route"] = "verify"
            _listen_clear(vars)
            return
    if not answer:
        vars["say_line"] = _question(vars)
        vars["route"] = "verify"
        _listen_clear(vars)
        return
    vars["verify_field"] = field
    vars["verify_answer"] = answer
    vars["gate_ok"] = "yes"
    vars["instruction"] = "Wait for the security result. Do not say the check passed yet."
    vars["route"] = "verify_call"
    _listen_clear(vars)


def run_verify_tally(vars):
    vars["op"] = "verify_tally"
    hint = str(vars.get("vi_hint") or "")
    if hint == "day_month_swapped":
        vars["say_line"] = CTX.get("hint_prompt") or ""
        vars["route"] = "verify"
        vars["gate_ok"] = "no"
        return
    if _truthy(vars.get("vi_all")):
        vars["verified"] = "yes"
        vars["gate_ok"] = "yes"
        vars["phase"] = "account"
        vars["route"] = "account"
        vars["instruction"] = "Identity is verified. Do not state the debt until the account tool returns."
        return
    if _truthy(vars.get("vi_verified")):
        index = int(vars.get("verify_stage_i") or "0") + 1
        fields = CTX.get("q_field") or []
        if index >= len(fields):
            vars["verified"] = "yes"
            vars["gate_ok"] = "yes"
            vars["route"] = "account"
            return
        vars["verify_stage_i"] = str(index)
        vars["say_line"] = str((CTX.get("q_prompt") or [""])[index])
        vars["last_ask"] = vars["say_line"]
        vars["route"] = "verify"
        vars["gate_ok"] = "no"
        return
    left = str(vars.get("vi_left") or "")
    if left == "0":
        _close(vars, "not_verified", "failed", CTX.get("fail_sentence") or "")
        return
    vars["say_line"] = str(CTX.get("wrong_prompt") or "") + " " + _question(vars)
    vars["route"] = "verify"
    vars["gate_ok"] = "no"


def _label_for(ptype, last4):
    types = CTX.get("label_type") or []
    texts = CTX.get("label_text") or []
    flags = CTX.get("label_last4") or []
    index = 0
    while index < len(types):
        if str(types[index]) == str(ptype):
            label = str(texts[index])
            if index < len(flags) and flags[index] == "yes":
                label = _fill(label, [("last4", last4 or "")])
            return label
        index = index + 1
    return str(ptype or "account").replace("_", " ")


def _floor_field(ptype):
    types = CTX.get("floor_type") or []
    fields = CTX.get("floor_field") or []
    index = 0
    while index < len(types):
        if str(types[index]) == str(ptype):
            return str(fields[index])
        index = index + 1
    return ""


def _as_products(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def run_account(vars):
    vars["op"] = "account"
    if str(vars.get("verified") or "") != "yes":
        vars["gate_ok"] = "no"
        _close(vars, "not_verified", "failed", CTX.get("fail_sentence") or "")
        return
    if not _truthy(vars.get("acct_ok")):
        tries = int(vars.get("account_tries") or "0")
        if tries < 1:
            vars["account_tries"] = "1"
            vars["gate_ok"] = "yes"
            vars["route"] = "account"
            vars["instruction"] = "The account could not be read. Do not state a balance."
            return
        _close(vars, "no_promise", "service_error", CTX.get("could_not") or "")
        return
    raw_rows = _as_products(vars.get("account_products"))
    if not raw_rows:
        raw_rows = _as_products(vars.get("get_account_response.data.products"))
    built = []
    today = _today(vars)
    index = 0
    while index < len(raw_rows):
        raw = raw_rows[index] if isinstance(raw_rows[index], dict) else {}
        built.append(_build_product(raw, index, today))
        index = index + 1
    if not built:
        _close(vars, "no_promise", "service_error", CTX.get("could_not") or "")
        return
    _save_products(vars, built)
    vars["neg_mode"] = "ask_today"
    vars["phase"] = "negotiate"
    vars["say_line"] = _debt_script(built)
    vars["last_ask"] = vars["say_line"]
    vars["route"] = "negotiate"
    vars["gate_ok"] = "no"


def _build_product(raw, index, today):
    row = {}
    row["product_id"] = str(raw.get("product_id") or ("p" + str(index + 1)))
    row["type"] = str(raw.get("type") or "")
    try:
        row["past_due"] = float(raw.get("past_due") or 0)
    except Exception:
        row["past_due"] = 0.0
    try:
        row["days_late"] = int(raw.get("days_late") or 0)
    except Exception:
        row["days_late"] = 0
    field = _floor_field(row["type"])
    floor_raw = raw.get(field) if field else None
    try:
        row["floor"] = float(floor_raw if floor_raw is not None else row["past_due"])
    except Exception:
        row["floor"] = row["past_due"]
    last4 = str(raw.get("card_last4") or "")
    row["label"] = _label_for(row["type"], last4)
    found = band_tuple(row["days_late"], CTX.get("band_min") or [], CTX.get("band_max") or [], CTX.get("band_bd") or [], CTX.get("band_attempts") or [], CTX.get("band_name") or [])
    if found:
        row["band"] = found[0]
        row["attempts_max"] = found[2]
        deadline = add_business_days(today, found[1], _holidays())
        row["deadline"] = deadline.isoformat()
    else:
        row["band"] = ""
        row["attempts_max"] = 1
        row["deadline"] = today.isoformat()
    row["attempts_used"] = 0
    row["status"] = "open"
    row["amount"] = ""
    row["date"] = ""
    row["too_much"] = "no"
    row["floor_offered"] = "no"
    row["vague_asked"] = "no"
    row["pending_amount"] = ""
    row["pending_date"] = ""
    row["adjust_note"] = ""
    sentence = _fill(CTX.get("sentence"), [("label", row["label"]), ("amount", _money(row["past_due"])), ("days", row["days_late"])])
    row["sentence"] = sentence
    return row


def _debt_script(rows):
    parts = [str(CTX.get("intro") or "")]
    for row in rows:
        parts.append(str(row.get("sentence") or ""))
    if len(rows) > 1:
        parts.append(str(CTX.get("today_all") or ""))
    else:
        parts.append(str(CTX.get("today_one") or ""))
    return " ".join(part for part in parts if part)


def _human(vars, repeat):
    count = int(vars.get("human_count") or "0") + 1
    vars["human_count"] = str(count)
    if count >= 2:
        _arm_transfer(vars, CTX.get("queue_human"), CTX.get("reason_human"))
        return
    vars["say_line"] = str(CTX.get("help_first") or "")
    vars["last_ask"] = repeat or vars["say_line"]
    vars["route"] = "negotiate" if vars.get("phase") == "negotiate" else "verify"


def _arm_transfer(vars, queue, reason):
    rows = _products(vars)
    index = 0
    while index < len(rows):
        if rows[index].get("status") in ("agreed", "confirmed"):
            rows[index]["status"] = "dropped"
        index = index + 1
    _save_products(vars, rows)
    vars["gate_ok"] = "yes"
    vars["gate_queue"] = str(queue)
    add_item(vars, "transfers_json", {"queue": str(queue), "reason": str(reason)})
    vars["close_outcome"] = "transferred"
    vars["close_reason"] = str(reason)
    vars["instruction"] = "Do not describe a plan. The transfer has not happened until the tool succeeds."
    vars["route"] = "transfer_call"
    vars["phase"] = "close"


def _current_open(rows, hint, utter):
    target = _match_product(rows, hint, utter)
    if target:
        for row in rows:
            if row.get("product_id") == target and row.get("status") == "open":
                return row
    for row in rows:
        if row.get("status") == "open":
            return row
    return None


def _match_product(rows, hint, utter):
    text = (str(hint or "") + " " + str(utter or "")).lower()
    for row in rows:
        ptype = str(row.get("type") or "").replace("_", " ")
        if ptype and ptype in text:
            return row.get("product_id")
    if "card" in text:
        for row in rows:
            if row.get("type") == "credit_card":
                return row.get("product_id")
    if "car" in text:
        for row in rows:
            if row.get("type") == "car_loan":
                return row.get("product_id")
    return ""


def _set_pending(row, amount, day):
    row["pending_amount"] = _money(amount)
    row["pending_date"] = day.isoformat() if hasattr(day, "isoformat") else str(day)


def _agree(row, amount, day, note=""):
    row["status"] = "agreed"
    row["amount"] = _money(amount)
    row["date"] = day.isoformat() if hasattr(day, "isoformat") else str(day)
    row["adjust_note"] = note


def _deadline(row):
    return parse_iso_date(row.get("deadline"))


def _offer_line(row, day, amount, kind):
    spoken = speak_date(day)
    if kind == "deadline":
        return _fill(CTX.get("latest_date"), [("date", spoken)]) + " The amount would be " + _money(amount) + "."
    if kind == "next_business":
        return _fill(CTX.get("next_bd_line"), [("date", spoken)]) + " The amount would be " + _money(amount) + "."
    if kind == "floor":
        return "The amount I can record is " + _money(amount) + " on " + spoken + ". " + str(CTX.get("floor_rest") or "")
    return "I can record " + _money(amount) + " on " + spoken + "."


def _begin_attempt(row, today):
    if int(row.get("attempts_used") or 0) >= int(row.get("attempts_max") or 1):
        row["status"] = "refused"
        return ""
    row["attempts_used"] = int(row.get("attempts_used") or 0) + 1
    deadline = _deadline(row)
    if int(row.get("attempts_used") or 0) == 1:
        day = attempt_date(today, _holidays())
        amount = row["floor"] if row.get("too_much") == "yes" else row["past_due"]
        action, fitted = fit_named_date(day, today, deadline, _holidays())
        if fitted is not None:
            day = fitted
        if row.get("too_much") == "yes":
            row["floor_offered"] = "yes"
            kind = "floor"
        else:
            kind = "attempt"
        _set_pending(row, amount, day)
        prefix = "For the " + str(row.get("label") or "account") + ", "
        return prefix + _offer_line(row, day, amount, kind)
    day = deadline or today
    amount = row["floor"]
    row["floor_offered"] = "yes"
    _set_pending(row, amount, day)
    prefix = "For the " + str(row.get("label") or "account") + ", "
    return prefix + _offer_line(row, day, amount, "floor")


def _advance(vars, rows, today):
    if any(row.get("status") == "open" for row in rows):
        current = None
        for row in rows:
            if row.get("status") == "open":
                current = row
                break
        if vars.get("neg_mode") == "ask_today" and _open_count(rows) == len(rows):
            vars["say_line"] = CTX.get("today_one") if len(rows) == 1 else CTX.get("today_all")
            vars["route"] = "negotiate"
            return
        line = _begin_attempt(current, today)
        vars["neg_mode"] = "propose"
        if current.get("status") == "refused":
            _advance(vars, rows, today)
            return
        vars["say_line"] = line
        vars["last_ask"] = line
        vars["route"] = "negotiate"
        return
    if any(row.get("status") == "agreed" for row in rows):
        vars["say_line"] = _readback(rows)
        vars["last_ask"] = vars["say_line"]
        vars["phase"] = "confirm"
        vars["route"] = "confirm"
        return
    _finish_without_promise(vars, rows)


def _open_count(rows):
    return len([row for row in rows if row.get("status") == "open"])


def _readback(rows):
    bits = []
    for row in rows:
        if row.get("status") in ("agreed", "confirmed"):
            day = parse_iso_date(row.get("date"))
            piece = _money(row.get("amount")) + " on " + speak_date(day) + " for the " + str(row.get("label") or "")
            if row.get("adjust_note"):
                piece = piece + " (" + str(row.get("adjust_note")) + ")"
            bits.append(piece)
    if not bits:
        return str(CTX.get("confirm_ask") or "")
    if len(bits) == 1:
        joined = bits[0]
    else:
        joined = ", ".join(bits[:-1]) + " and " + bits[-1]
    return "You are agreeing to pay " + joined + ". " + str(CTX.get("confirm_ask") or "")


def _finish_without_promise(vars, rows):
    if CTX.get("notes") == "yes":
        vars["route"] = "notes"
        return
    explicit = str(vars.get("close_outcome") or "")
    if explicit:
        vars["route"] = "close"
        return
    outcome, reason = resolve(rows, [], [], [], "", "", False)
    _close(vars, outcome, reason)


def _mark_status(rows, product_id, status):
    for row in rows:
        if product_id and row.get("product_id") != product_id:
            continue
        if row.get("status") == "open":
            row["status"] = status


def _apply_date_amount(row, named, amount, today, auto):
    deadline = _deadline(row)
    action, fitted = fit_named_date(named, today, deadline, _holidays())
    use_amount = amount if amount is not None else (row["floor"] if row.get("too_much") == "yes" else row["past_due"])
    try:
        use_amount = float(use_amount)
    except Exception:
        return "bad", ""
    if use_amount + 0.001 < float(row["floor"]):
        if row.get("floor_offered") == "yes":
            return "floor_refused", ""
        row["floor_offered"] = "yes"
        row["too_much"] = "yes"
        day = fitted or named
        if day is None or not hasattr(day, "isoformat"):
            day = attempt_date(today, _holidays())
        _set_pending(row, row["floor"], day)
        return "floor", _offer_line(row, day, row["floor"], "floor")
    if use_amount > float(row["past_due"]) + 0.001:
        use_amount = float(row["past_due"])
    if action == "ok":
        _agree(row, use_amount, fitted)
        return "agreed", ""
    if action in ("deadline", "next_business") and fitted is not None:
        if auto:
            note = "recorded on the latest date" if action == "deadline" else "moved to the next business day"
            _agree(row, use_amount, fitted, note)
            return "agreed", ""
        _set_pending(row, use_amount, fitted)
        return "offer", _offer_line(row, fitted, use_amount, "deadline" if action == "deadline" else "next_business")
    return "bad", ""


def run_negotiate(vars):
    vars["op"] = "negotiate"
    rows = _products(vars)
    today = _today(vars)
    utter = str(vars.get("utterance") or "")
    enum = str(vars.get("n_enum") or "")
    blob = utter + " " + str(vars.get("n_date") or "")
    kind, named = parse_when(blob, today, CTX.get("vague") or [])
    amount = _amount(vars.get("n_amount"), utter)
    if _has(utter, "manipulation") or enum == "manipulation":
        vars["say_line"] = (CTX.get("manip_sentence") or "") + " " + str(vars.get("last_ask") or "")
        vars["route"] = "negotiate"
        _save_products(vars, rows)
        _listen_clear(vars)
        return
    if _has(utter, "ai"):
        vars["say_line"] = (CTX.get("ai_sentence") or "") + " " + str(vars.get("last_ask") or "")
        vars["route"] = "negotiate"
        _listen_clear(vars)
        return
    if (_has(utter, "hardship") or enum == "hardship") and kind != "exact" and amount is None:
        _hardship(vars, rows)
        _listen_clear(vars)
        return
    if enum == "restructuring" or _has(utter, "restructuring"):
        _save_products(vars, rows)
        _arm_transfer(vars, CTX.get("queue_restructuring"), "restructuring")
        _listen_clear(vars)
        return
    if enum == "human" or _has(utter, "human"):
        _human(vars, vars.get("last_ask") or "")
        _save_products(vars, rows)
        _listen_clear(vars)
        return
    if enum == "stop" or _has(utter, "stop"):
        count = int(vars.get("stop_count") or "0") + 1
        vars["stop_count"] = str(count)
        if count >= 2:
            for row in rows:
                if row.get("status") == "open":
                    row["status"] = "refused"
            _save_products(vars, rows)
            _close(vars, "no_promise", "refused")
        else:
            vars["say_line"] = str(vars.get("last_ask") or "")
            vars["route"] = "negotiate"
        _listen_clear(vars)
        return
    if vars.get("support_mode") == "yes":
        if enum in ("accept", "yes") or _has(utter, "yes"):
            _save_products(vars, rows)
            _arm_transfer(vars, CTX.get("queue_support"), "support")
        elif kind == "exact" or amount is not None:
            vars["support_mode"] = "no"
        else:
            for row in rows:
                if row.get("status") == "open":
                    row["status"] = "hardship"
            _save_products(vars, rows)
            _close(vars, "no_promise", "hardship")
            _listen_clear(vars)
            return
    target = _match_product(rows, vars.get("n_product"), utter)
    multi = len(rows) > 1
    if enum in ("dispute",) or _has(utter, "dispute"):
        _mark_many(rows, target, "dispute")
        _save_products(vars, rows)
        if CTX.get("dispute_continues") != "yes":
            _close(vars, "no_promise", "dispute")
        else:
            _advance(vars, rows, today)
        _listen_clear(vars)
        return
    if enum in ("already_paid", "paid") or _has(utter, "paid"):
        _mark_many(rows, target, "paid")
        _save_products(vars, rows)
        if CTX.get("paid_continues") != "yes":
            _close(vars, "paid", "", CTX.get("paid_delay") or "")
        else:
            _advance(vars, rows, today)
        _listen_clear(vars)
        return
    if (enum == "too_much" or "too much" in utter.lower()) and kind != "exact":
        current = _current_open(rows, vars.get("n_product"), utter)
        if current is not None:
            current["too_much"] = "yes"
            line = _begin_attempt(current, today)
            vars["neg_mode"] = "propose"
            vars["say_line"] = line
            vars["last_ask"] = line
            vars["route"] = "negotiate"
            if current.get("status") == "refused":
                _advance(vars, rows, today)
            _save_products(vars, rows)
            _listen_clear(vars)
            return
    if kind == "vague":
        current = _current_open(rows, vars.get("n_product"), utter)
        if current is not None and current.get("vague_asked") != "yes":
            current["vague_asked"] = "yes"
            vars["say_line"] = CTX.get("exact_day") or ""
            vars["last_ask"] = vars["say_line"]
            vars["neg_mode"] = "exact"
            vars["route"] = "negotiate"
            _save_products(vars, rows)
            _listen_clear(vars)
            return
        if current is not None:
            deadline = _deadline(current)
            _set_pending(current, current["past_due"] if current.get("too_much") != "yes" else current["floor"], deadline or today)
            vars["say_line"] = _offer_line(current, deadline or today, current["pending_amount"], "deadline")
            vars["neg_mode"] = "propose"
            vars["route"] = "negotiate"
            _save_products(vars, rows)
            _listen_clear(vars)
            return
    if kind == "exact" or amount is not None or enum in ("offer_date", "offer_amount", "too_much", "accept"):
        if enum == "too_much" or _has(utter, "too_much"):
            for row in rows:
                if row.get("status") == "open" and (not target or row.get("product_id") == target):
                    row["too_much"] = "yes"
        if enum == "accept" and kind != "exact" and amount is None and vars.get("neg_mode") == "propose":
            _accept_pending(rows, target)
            _save_products(vars, rows)
            _advance(vars, rows, today)
            _listen_clear(vars)
            return
        if enum == "accept" and kind != "exact" and amount is None and vars.get("neg_mode") == "ask_today":
            _agree_today(rows, today, target)
            _save_products(vars, rows)
            _advance(vars, rows, today)
            _listen_clear(vars)
            return
        if kind == "exact":
            auto = multi and not target
            lines = []
            for row in rows:
                if row.get("status") != "open":
                    continue
                if target and row.get("product_id") != target:
                    continue
                status, line = _apply_date_amount(row, named, amount, today, auto)
                if line:
                    lines.append("For the " + str(row.get("label") or "") + ", " + line)
                if status == "floor_refused":
                    row["status"] = "refused"
            _save_products(vars, rows)
            if lines and any(row.get("status") == "open" and row.get("pending_date") for row in rows):
                vars["say_line"] = " ".join(lines)
                vars["last_ask"] = vars["say_line"]
                vars["neg_mode"] = "propose"
                vars["route"] = "negotiate"
                _listen_clear(vars)
                return
            _advance(vars, rows, today)
            _listen_clear(vars)
            return
        if amount is not None:
            current = _current_open(rows, vars.get("n_product"), utter)
            if current is not None:
                status, line = _apply_date_amount(current, named, amount, today, False)
                _save_products(vars, rows)
                if line:
                    vars["say_line"] = line
                    vars["neg_mode"] = "propose"
                    vars["route"] = "negotiate"
                else:
                    _advance(vars, rows, today)
                _listen_clear(vars)
                return
    if enum in ("refuse", "cannot_today", "unclear") or _has(utter, "decline"):
        if vars.get("neg_mode") == "ask_today":
            vars["neg_mode"] = "propose"
        current = _current_open(rows, "", "")
        if current is not None and current.get("pending_date") and vars.get("neg_mode") == "propose":
            line = _begin_attempt(current, today)
            if current.get("status") == "refused":
                _save_products(vars, rows)
                _advance(vars, rows, today)
            else:
                vars["say_line"] = line
                vars["last_ask"] = line
                vars["route"] = "negotiate"
                _save_products(vars, rows)
            _listen_clear(vars)
            return
        if current is not None:
            line = _begin_attempt(current, today)
            vars["neg_mode"] = "propose"
            if current.get("status") == "refused":
                _save_products(vars, rows)
                _advance(vars, rows, today)
            else:
                vars["say_line"] = line
                vars["last_ask"] = line
                vars["route"] = "negotiate"
                _save_products(vars, rows)
            _listen_clear(vars)
            return
    vars["say_line"] = str(vars.get("last_ask") or CTX.get("today_one") or "")
    vars["route"] = "negotiate"
    _save_products(vars, rows)
    _listen_clear(vars)


def _amount(raw, utter):
    if raw not in (None, "", "pending"):
        try:
            return float(raw)
        except Exception:
            pass
    return None


def _hardship(vars, rows):
    vars["support_mode"] = "yes"
    vars["say_line"] = str(CTX.get("sympathy") or "") + " " + str(CTX.get("support_offer") or "")
    vars["last_ask"] = vars["say_line"]
    vars["route"] = "negotiate"
    _save_products(vars, rows)


def _accept_pending(rows, target):
    for row in rows:
        if row.get("status") != "open":
            continue
        if target and row.get("product_id") != target:
            continue
        if row.get("pending_date"):
            _agree(row, row.get("pending_amount"), parse_iso_date(row.get("pending_date")) or row.get("pending_date"))


def _agree_today(rows, today, target):
    for row in rows:
        if row.get("status") != "open":
            continue
        if target and row.get("product_id") != target:
            continue
        amount = row["floor"] if row.get("too_much") == "yes" else row["past_due"]
        status, _line = _apply_date_amount(row, today, amount, today, True)


def _mark_many(rows, target, status):
    if target:
        _mark_status(rows, target, status)
        return
    for row in rows:
        if row.get("status") == "open":
            row["status"] = status


def run_confirm(vars):
    vars["op"] = "confirm"
    rows = _products(vars)
    utter = str(vars.get("utterance") or "")
    enum = str(vars.get("c_enum") or "")
    today = _today(vars)
    kind, named = parse_when(utter + " " + str(vars.get("c_date") or ""), today, CTX.get("vague") or [])
    amount = _amount(vars.get("c_amount"), utter)
    if enum in ("no", "change") or kind == "exact" or amount is not None:
        if kind == "exact" or amount is not None:
            for row in rows:
                if row.get("status") in ("agreed", "confirmed"):
                    row["status"] = "open"
            for row in rows:
                if row.get("status") != "open":
                    continue
                if kind == "exact":
                    _apply_date_amount(row, named, amount, today, len(rows) > 1)
                elif amount is not None:
                    day = parse_iso_date(row.get("date")) or today
                    _apply_date_amount(row, day, amount, today, True)
            _save_products(vars, rows)
            vars["say_line"] = _readback(rows)
            vars["route"] = "confirm"
            _listen_clear(vars)
            return
        vars["say_line"] = str(vars.get("last_ask") or "")
        vars["route"] = "negotiate"
        vars["phase"] = "negotiate"
        _listen_clear(vars)
        return
    if enum == "yes" or _has(utter, "yes"):
        for row in rows:
            if row.get("status") == "agreed":
                row["status"] = "confirmed"
        _save_products(vars, rows)
        vars["promise_tries"] = "0"
        _listen_clear(vars)
        run_record_plan(vars)
        return
    vars["say_line"] = _readback(rows)
    vars["route"] = "confirm"
    _listen_clear(vars)


def run_record_plan(vars):
    vars["op"] = "record_plan"
    rows = _products(vars)
    for row in rows:
        if row.get("status") == "confirmed":
            vars["gate_ok"] = "yes"
            vars["gate_product_id"] = str(row.get("product_id") or "")
            vars["gate_amount"] = str(row.get("amount") or "")
            vars["gate_date"] = str(row.get("date") or "")
            vars["active_product"] = vars["gate_product_id"]
            vars["instruction"] = "Do not say the promise is recorded yet."
            vars["route"] = "record_call"
            return
    vars["gate_ok"] = "no"
    if _any_recorded(rows):
        parts = []
        if str(vars.get("extra_line") or "").strip():
            parts.append(str(vars.get("extra_line")).strip())
        recorded = str(CTX.get("recorded") or "").strip()
        sms = str(CTX.get("sms") or "").strip()
        if recorded:
            parts.append(recorded)
        if sms:
            parts.append(sms)
        vars["say_line"] = " ".join(parts)
        vars["extra_line"] = ""
        vars["route"] = "sms"
        return
    if CTX.get("notes") == "yes":
        vars["route"] = "notes"
        return
    if not vars.get("close_outcome"):
        outcome, reason = resolve(rows, load_json(vars, "promises_json"), [], [], "", "", False)
        vars["close_outcome"] = outcome
        vars["close_reason"] = reason
        if outcome == "no_promise" and reason == "service_error":
            vars["extra_line"] = CTX.get("could_not") or ""
    vars["route"] = "close"


def _any_recorded(rows):
    for row in rows:
        if row.get("status") == "recorded":
            return True
    return False


def run_record_tally(vars):
    vars["op"] = "record_tally"
    rows = _products(vars)
    pid = str(vars.get("active_product") or "")
    if _truthy(vars.get("pr_ok")):
        for row in rows:
            if str(row.get("product_id")) == pid:
                row["status"] = "recorded"
                add_item(vars, "promises_json", {
                    "product_id": pid,
                    "amount": row.get("amount"),
                    "date": row.get("date"),
                    "ok": "yes",
                })
        vars["promise_tries"] = "0"
        vars["instruction"] = "The promise is recorded."
        _save_products(vars, rows)
        run_record_plan(vars)
        return
    tries = int(vars.get("promise_tries") or "0")
    if tries < 1:
        vars["promise_tries"] = "1"
        vars["gate_ok"] = "yes"
        vars["instruction"] = "The promise was not recorded. Do not say it was recorded."
        vars["route"] = "record_call"
        return
    for row in rows:
        if str(row.get("product_id")) == pid:
            row["status"] = "service_error"
    vars["promise_tries"] = "0"
    vars["extra_line"] = CTX.get("could_not") or ""
    vars["instruction"] = "The promise was not recorded."
    _save_products(vars, rows)
    run_record_plan(vars)


def run_notes(vars):
    vars["op"] = "notes"
    if load_json(vars, "transfers_json"):
        vars["route"] = "close"
        return
    if CTX.get("notes") != "yes":
        run_close(vars)
        return
    rows = _products(vars)
    noted = load_json(vars, "notes_json")
    for row in rows:
        status = str(row.get("status") or "")
        if status not in ("refused", "hardship", "dispute", "paid", "service_error"):
            continue
        if has_product(noted, row.get("product_id")):
            continue
        vars["gate_ok"] = "yes"
        vars["gate_product_id"] = str(row.get("product_id") or "")
        vars["gate_reason"] = status
        vars["gate_text"] = ""
        vars["active_product"] = vars["gate_product_id"]
        vars["instruction"] = "A note is being saved. Do not invent a promise."
        vars["route"] = "note_call"
        return
    vars["gate_ok"] = "no"
    run_close(vars)


def run_note_tally(vars):
    vars["op"] = "note_tally"
    if _truthy(vars.get("note_ok")):
        add_item(vars, "notes_json", {"product_id": str(vars.get("active_product") or ""), "reason": str(vars.get("gate_reason") or "")})
        vars["note_tries"] = "0"
        run_notes(vars)
        return
    tries = int(vars.get("note_tries") or "0")
    if tries < 1:
        vars["note_tries"] = "1"
        vars["gate_ok"] = "yes"
        vars["route"] = "note_call"
        vars["instruction"] = "The note was not saved."
        return
    vars["note_tries"] = "0"
    add_item(vars, "notes_json", {"product_id": str(vars.get("active_product") or ""), "reason": str(vars.get("gate_reason") or ""), "local_only": "yes"})
    run_notes(vars)


def run_transfer_tally(vars):
    vars["op"] = "transfer_tally"
    if _truthy(vars.get("tr_ok")):
        vars["route"] = "close"
        vars["extra_line"] = ""
        return
    tries = int(vars.get("transfer_tries") or "0")
    if tries < 1:
        vars["transfer_tries"] = "1"
        vars["gate_ok"] = "yes"
        vars["route"] = "transfer_call"
        vars["instruction"] = "The transfer did not go through. Do not say the customer is transferred."
        return
    vars["close_outcome"] = "no_promise"
    vars["close_reason"] = "service_error"
    vars["extra_line"] = CTX.get("could_not") or ""
    save_json(vars, "transfers_json", [])
    vars["route"] = "close"


def run_close(vars):
    vars["op"] = "close"
    rows = _products(vars)
    outcome, reason = resolve(
        rows,
        load_json(vars, "promises_json"),
        load_json(vars, "notes_json"),
        load_json(vars, "transfers_json"),
        str(vars.get("close_outcome") or ""),
        str(vars.get("close_reason") or ""),
        CTX.get("notes") == "yes",
    )
    vars["close_outcome"] = outcome
    vars["close_reason"] = reason
    vars["gate_ok"] = "yes"
    if str(vars.get("extra_line") or "").strip():
        vars["route"] = "extra"
    else:
        vars["route"] = "thanks"
    if outcome == "paid" and not str(vars.get("extra_line") or "").strip():
        vars["extra_line"] = CTX.get("paid_delay") or ""
        vars["route"] = "extra"


def run_breaker(vars):
    vars["op"] = "breaker"
    phase = str(vars.get("phase") or "")
    if phase in ("opening", "third"):
        if not vars.get("close_outcome"):
            _close(vars, "not_reached", "unclear")
    elif phase == "verify":
        if not vars.get("close_outcome"):
            _close(vars, "not_verified", "failed", CTX.get("fail_sentence") or "")
    else:
        rows = _products(vars)
        for row in rows:
            if row.get("status") == "open":
                row["status"] = "refused"
        _save_products(vars, rows)
        if not vars.get("close_outcome"):
            _close(vars, "no_promise", "refused")
    if vars.get("route") != "close":
        vars["route"] = "close"


def run_engine(vars):
    op = str(vars.get("op_next") or vars.get("op") or "boot")
    dispatch = {
        "boot": run_boot,
        "opening": run_opening,
        "third": run_third,
        "callback_tally": run_callback_tally,
        "verify_prep": run_verify_prep,
        "verify_tally": run_verify_tally,
        "account": run_account,
        "negotiate": run_negotiate,
        "confirm": run_confirm,
        "record_plan": run_record_plan,
        "record_tally": run_record_tally,
        "notes": run_notes,
        "note_tally": run_note_tally,
        "transfer_tally": run_transfer_tally,
        "close": run_close,
        "breaker": run_breaker,
    }
    fn = dispatch.get(op) or run_boot
    fn(vars)
