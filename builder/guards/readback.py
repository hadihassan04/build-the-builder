"""Deterministic date fitting + spoken read-back for promises.

Code nodes compiled from this module (Freya sandbox: every helper is embedded, no module
globals, no doubled braces):

- fit_dates:          customer named a date (or "today") → per-product clamp/shift → read-back TTS
- handle_vague:       vague period → ask the exact day once, then propose the deadline(s)
- accept_persuasion:  customer accepted a scripted persuasion offer → read-back TTS
- ack_promise_code:   consume one-shot flags after create_promise ok

Brief A rule 20/21/23/34, Brief B rule 6/7: the dialogue model never chooses dates;
`fitted_dates_json` / `fitted_amounts_json` are enforced by the create_promise pre-hook.
"""
from __future__ import annotations

import datetime
import inspect
import json

RB_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _rb_load_map(raw):
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _rb_load_list(raw):
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _rb_parse(iso):
    raw = str(iso or "").strip()
    if len(raw) < 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return datetime.date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10]))
    except Exception:
        return None


def _rb_today(vars):
    day = _rb_parse(str(vars.get("now") or ""))
    if day is None:
        return datetime.date(2026, 1, 1)
    return day


def _rb_holidays(vars):
    raw = vars.get("policy_holidays") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    out = []
    for item in raw or []:
        out.append(str(item))
    return out


def _rb_is_bd(day, holidays):
    if day is None or day.weekday() >= 5:
        return False
    return day.isoformat() not in holidays


def _rb_next_bd(day, holidays):
    nxt = day + datetime.timedelta(days=1)
    guard = 0
    while not _rb_is_bd(nxt, holidays) and guard < 30:
        nxt = nxt + datetime.timedelta(days=1)
        guard = guard + 1
    return nxt


def _rb_speak(day):
    if day is None:
        return ""
    return str(day.day) + " " + RB_MONTHS[day.month] + " " + str(day.year)


def _rb_money(value):
    try:
        return "%.2f" % float(value)
    except Exception:
        return str(value or "")


def _rb_ai_prefix(vars):
    """Customer asked whether this is a robot in the same turn a TTS node will speak."""
    if str(vars.get("asked_if_ai") or "").lower() in ("yes", "true"):
        line = str(vars.get("ai_disclosure") or "I am Freya Bank's digital assistant.")
        vars["ai_prefix"] = line + " "
    else:
        vars["ai_prefix"] = ""
    vars["asked_if_ai"] = "no"


def _rb_promised(vars):
    """Promised pids from the JSON list plus race-safe per-pid markers."""
    out = []
    for pid in _rb_load_list(vars.get("promised_products")):
        sp = str(pid)
        if sp and sp not in out:
            out.append(sp)
    for pid in _rb_load_list(vars.get("allowed_product_ids")):
        sp = str(pid)
        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in sp)[:64]
        if safe and str(vars.get("promised_" + safe) or "") == "yes" and sp not in out:
            out.append(sp)
    return out


def _rb_noted(vars):
    out = []
    for pid in _rb_load_list(vars.get("noted_products")):
        sp = str(pid)
        if sp and sp not in out:
            out.append(sp)
    for row in _rb_load_list(vars.get("ledger_notes")):
        if isinstance(row, dict) and row.get("product_id"):
            sp = str(row.get("product_id"))
            if sp not in out:
                out.append(sp)
    return out


def _rb_open_ids(vars):
    ids = [str(x) for x in _rb_load_list(vars.get("allowed_product_ids"))]
    promised = _rb_promised(vars)
    noted = _rb_noted(vars)
    open_ids = []
    for pid in ids:
        if pid in promised or pid in noted:
            continue
        open_ids.append(pid)
    if not open_ids:
        open_ids = ids
    return ids, open_ids


def _rb_set_readback(vars, open_ids, fitted, amounts, kinds, named_day):
    """Write readback_tts (spoken) + date_readback (for the model) + fitted maps."""
    labels = _rb_load_map(vars.get("labels_json"))
    holidays = _rb_holidays(vars)
    sentences = []
    parts = []
    for pid in open_ids:
        iso = str(fitted.get(pid) or "")
        day = _rb_parse(iso)
        if day is None:
            continue
        label = str(labels.get(pid) or pid)
        amt = _rb_money(amounts.get(pid))
        spoken = _rb_speak(day)
        kind = str(kinds.get(pid) or "ok")
        if kind == "clamped":
            sentences.append(
                "For your " + label + ", the latest date I can record is " + spoken
                + ", so " + amt + " on " + spoken + "."
            )
        elif kind == "shifted" and named_day is not None:
            why = "is a bank holiday" if named_day.isoformat() in holidays else "falls on a weekend"
            sentences.append(
                _rb_speak(named_day) + " " + why + ", so for your " + label
                + " the next business day is " + spoken + ": " + amt + " on " + spoken + "."
            )
        else:
            sentences.append("For your " + label + ", " + amt + " on " + spoken + ".")
        parts.append(pid + " amount " + amt + " on " + iso)
    vars["fitted_dates_json"] = json.dumps(fitted)
    vars["fitted_amounts_json"] = json.dumps(amounts)
    vars["date_readback"] = "; ".join(parts)
    vars["readback_tts"] = str(vars.get("ai_prefix") or "") + "To confirm: " + " ".join(sentences) + " Do you confirm?"
    vars["need_readback"] = "yes" if parts else "no"
    vars["gate_ok"] = "no"
    vars["deadline_refused"] = "no"
    vars["instruction"] = (
        "The read-back was spoken by TTS. On a clear yes CALL create_promise once for EACH of: "
        + vars["date_readback"]
        + " (exact product_id, amount, date), all in the same turn. Never say recorded yourself."
    )


def _rb_stage_deadline_offer(vars, pid, lim_day, due):
    """Brief A rule 23 (single product): speak the deadline once with the full amount."""
    vars["deadline_offered"] = "yes"
    vars["deadline_refused"] = "no"
    vars["need_deadline_offer"] = "yes"
    vars["gate_product_id"] = pid
    vars["gate_date"] = lim_day.isoformat()
    vars["gate_amount"] = _rb_money(due)
    vars["gate_ok"] = "yes"
    fitted = {}
    fitted[pid] = lim_day.isoformat()
    amounts = {}
    amounts[pid] = _rb_money(due)
    vars["fitted_dates_json"] = json.dumps(fitted)
    vars["fitted_amounts_json"] = json.dumps(amounts)
    vars["date_readback"] = pid + " amount " + _rb_money(due) + " on " + lim_day.isoformat()
    vars["deadline_offer_tts"] = (
        str(vars.get("ai_prefix") or "")
        + "The latest date I can record is " + _rb_speak(lim_day)
        + ". Can you pay the full " + _rb_money(due) + " by then?"
    )
    vars["instruction"] = (
        "Deadline was just offered by TTS. If they confirm, CALL create_promise. "
        "If they refuse or only repeat a later date, that is a flat refusal — do not loop."
    )


def fit_dates(vars):
    _rb_ai_prefix(vars)
    intent = str(vars.get("customer_intent") or "")
    vars["customer_intent"] = "other"
    vars["need_deadline_offer"] = "no"
    vars["need_readback"] = "no"
    holidays = _rb_holidays(vars)
    today = _rb_today(vars)
    deadlines = _rb_load_map(vars.get("deadlines_json"))
    dues = _rb_load_map(vars.get("past_dues_json"))
    floors = _rb_load_map(vars.get("floors_json"))
    ids, open_ids = _rb_open_ids(vars)
    multi = len(ids) > 1
    proposed = str(vars.get("proposed_date") or "")
    # Vague periods must ask the exact day first (Brief A.21). Freya often invents a
    # calendar day for "week after next" — only divert when there is no usable exact day.
    # A concrete proposed_date (e.g. Monday the 26th) always wins over a sticky vague flag.
    vague_flag = str(vars.get("period_is_vague") or "").lower() in ("yes", "true")
    already_asked = str(vars.get("exact_day_asked") or "") == "yes"
    parsed_proposed = _rb_parse(proposed)
    if (
        (intent == "vague_period" or vague_flag)
        and (not already_asked)
        and intent != "pays_today"
        and parsed_proposed is None
    ):
        vars["customer_intent"] = "vague_period"
        handle_vague(vars)
        return
    # pays_today = calendar today from `now`. Ignore a stale/past proposed_date
    # (extraction often fills a wrong day when the customer only said "today").
    if intent == "pays_today":
        prop_day = today
    else:
        prop_day = parsed_proposed
        if prop_day is not None and prop_day < today:
            # Mis-parsed "tomorrow" → next business day after today.
            prop_day = _rb_next_bd(today, holidays)
        # Invented date for a vague period (no exact day asked yet): ask once, do not fit.
        if vague_flag and (not already_asked) and intent == "proposes_date":
            vars["customer_intent"] = "vague_period"
            handle_vague(vars)
            return
    if proposed:
        vars["last_proposed_date"] = proposed
        vars["proposed_date_consumed"] = "yes"
    if prop_day is None:
        vars["deadline_refused"] = "no"
        vars["instruction"] = "No usable payment date was understood. Ask which exact day they can pay."
        return
    vars["proposed_date"] = prop_day.isoformat()
    vars["period_is_vague"] = "no"
    scope = str(vars.get("pay_scope") or "all").lower()
    if not multi:
        scope = "all"
    if scope == "some":
        # Customer limited the date to some products: the dialogue model reads back only those.
        parts = []
        for pid in open_ids:
            lim = _rb_parse(str(deadlines.get(pid) or ""))
            cand = prop_day
            if lim is not None and cand > lim:
                cand = lim
            if not _rb_is_bd(cand, holidays):
                nxt = _rb_next_bd(cand, holidays)
                cand = lim if (lim is not None and nxt > lim) else nxt
            parts.append(pid + " amount " + _rb_money(dues.get(pid)) + " on " + cand.isoformat())
        vars["fitted_dates_json"] = "{}"
        vars["fitted_amounts_json"] = "{}"
        vars["date_readback"] = "; ".join(parts)
        vars["deadline_refused"] = "no"
        vars["instruction"] = (
            "Customer named " + prop_day.isoformat() + " for SOME products only. Per-product dates if used: "
            + vars["date_readback"]
            + ". Read back only the products they will pay (product, amount, date), ask Do you confirm?, "
            "then CALL create_promise for each of those. Do not add notes; the workflow handles refused products."
        )
        return
    fitted = {}
    amounts = {}
    kinds = {}
    for pid in open_ids:
        lim = _rb_parse(str(deadlines.get(pid) or ""))
        cand = prop_day
        kind = "ok"
        if lim is not None and cand > lim:
            cand = lim
            kind = "clamped"
        if not _rb_is_bd(cand, holidays):
            nxt = _rb_next_bd(cand, holidays)
            if lim is not None and nxt > lim:
                cand = lim
                kind = "clamped"
            else:
                cand = nxt
                if kind == "ok":
                    kind = "shifted"
        fitted[pid] = cand.isoformat()
        kinds[pid] = kind
        amt = dues.get(pid)
        if not multi:
            try:
                offered = float(vars.get("proposed_amount") or 0)
                low = float(floors.get(pid) or 0)
                high = float(dues.get(pid) or 0)
                if offered > 0 and offered + 0.001 >= low and offered <= high + 0.001:
                    amt = offered
            except Exception:
                amt = dues.get(pid)
        amounts[pid] = amt
    if not multi and open_ids and kinds.get(open_ids[0]) == "clamped":
        pid = open_ids[0]
        lim = _rb_parse(str(deadlines.get(pid) or ""))
        if str(vars.get("deadline_offered") or "") == "yes":
            # Brief A rule 23: the deadline was already offered once → flat refusal.
            vars["deadline_refused"] = "yes"
            vars["customer_intent"] = "refuses"
            vars["gate_ok"] = "no"
            vars["fitted_dates_json"] = "{}"
            vars["fitted_amounts_json"] = "{}"
            vars["instruction"] = (
                "Customer refused the deadline after it was offered. Do not re-offer the deadline. "
                "Treat as flat refusal: persuasion then refused notes / close. "
                "Do not call create_promise for the late date."
            )
            return
        _rb_stage_deadline_offer(vars, pid, lim, dues.get(pid))
        return
    _rb_set_readback(vars, open_ids, fitted, amounts, kinds, prop_day)


def handle_vague(vars):
    """Brief A rule 21: ask once for the exact day; still vague → propose the deadline."""
    _rb_ai_prefix(vars)
    vars["customer_intent"] = "other"
    vars["period_is_vague"] = "no"
    vars["proposed_date_consumed"] = "yes"
    vars["need_deadline_offer"] = "no"
    vars["need_readback"] = "no"
    vars["ask_exact_day"] = "no"
    if str(vars.get("exact_day_asked") or "") != "yes":
        vars["exact_day_asked"] = "yes"
        vars["ask_exact_day"] = "yes"
        vars["vague_tts"] = str(vars.get("ai_prefix") or "") + "What exact day works for you?"
        vars["instruction"] = "You asked for the exact day. Wait for a specific date; do not propose one yet."
        return
    deadlines = _rb_load_map(vars.get("deadlines_json"))
    dues = _rb_load_map(vars.get("past_dues_json"))
    ids, open_ids = _rb_open_ids(vars)
    if not open_ids:
        return
    if len(ids) == 1:
        pid = open_ids[0]
        lim = _rb_parse(str(deadlines.get(pid) or ""))
        if lim is None:
            return
        _rb_stage_deadline_offer(vars, pid, lim, dues.get(pid))
        return
    fitted = {}
    amounts = {}
    kinds = {}
    for pid in open_ids:
        lim = _rb_parse(str(deadlines.get(pid) or ""))
        if lim is None:
            continue
        fitted[pid] = lim.isoformat()
        amounts[pid] = dues.get(pid)
        kinds[pid] = "clamped"
    _rb_set_readback(vars, open_ids, fitted, amounts, kinds, None)


def accept_persuasion(vars):
    """Customer said yes to the scripted offer → read it back once, then create_promise."""
    _rb_ai_prefix(vars)
    vars["customer_intent"] = "other"
    vars["need_readback"] = "no"
    focus = str(vars.get("persuasion_focus") or "")
    offer_date = _rb_parse(str(vars.get("persuasion_offer_date") or ""))
    if not focus or offer_date is None:
        vars["instruction"] = "Customer accepted the offer. Read product, amount and date back, ask Do you confirm?, then CALL create_promise."
        return
    fitted = _rb_load_map(vars.get("fitted_dates_json"))
    amounts = _rb_load_map(vars.get("fitted_amounts_json"))
    fitted[focus] = offer_date.isoformat()
    amounts[focus] = str(vars.get("persuasion_offer_amount") or "")
    kinds = {}
    kinds[focus] = "ok"
    _rb_set_readback(vars, [focus], fitted, amounts, kinds, None)


def ack_promise_code(vars):
    _rb_ai_prefix(vars)
    vars["promise_just_ok"] = "no"
    vars["gate_ok"] = "no"
    vars["need_readback"] = "no"
    vars["customer_intent"] = "other"


def _embed(entry_name):
    helpers = [
        _rb_load_map, _rb_load_list, _rb_parse, _rb_today, _rb_holidays, _rb_is_bd, _rb_next_bd,
        _rb_speak, _rb_money, _rb_ai_prefix, _rb_promised, _rb_noted, _rb_open_ids,
        _rb_set_readback, _rb_stage_deadline_offer, fit_dates, handle_vague, accept_persuasion,
        ack_promise_code,
    ]
    chunks = ["import datetime", "import json", "RB_MONTHS = " + json.dumps(RB_MONTHS)]
    for fn in helpers:
        chunks.append(inspect.getsource(fn))
    chunks.append(
        "try:\n"
        "    " + entry_name + "(vars)\n"
        "except Exception:\n"
        "    vars['need_readback'] = 'no'\n"
        "    vars['need_deadline_offer'] = 'no'\n"
        "    vars['ask_exact_day'] = 'no'\n"
        "    vars['deadline_refused'] = 'no'\n"
    )
    text = "\n".join(chunks)
    if "{{" in text or "}}" in text:
        raise RuntimeError("readback code has doubled braces")
    return text


FIT_DATES_CODE = _embed("fit_dates")
HANDLE_VAGUE_CODE = _embed("handle_vague")
ACCEPT_PERSUASION_CODE = _embed("accept_persuasion")
ACK_PROMISE_CODE = _embed("ack_promise_code")
