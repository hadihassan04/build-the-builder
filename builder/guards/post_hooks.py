"""post_process_code that tells the dialogue model what the tool result means."""
import json


def what_to_do_next(failure_line):
    failed = json.dumps(failure_line)
    text = "\n".join([
        "try:",
        "    if isinstance(result, dict):",
        "        result.pop('header', None)",
        "        data = result.get('data')",
        "        if not isinstance(data, dict):",
        "            data = {}",
        "            result['data'] = data",
        "        ok = data.get('ok')",
        "        bad = ok is False or str(ok).lower() == 'false'",
        "        nxt = str(vars.get('instruction') or '')",
        "        if bad:",
        "            data['what_to_do_next'] = " + failed,
        "        elif nxt:",
        "            data['what_to_do_next'] = nxt",
        "        else:",
        "            data['what_to_do_next'] = 'Continue. Do not invent figures or say something was recorded.'",
        "except Exception:",
        "    pass",
        "",
    ])
    if "{{" in text or "}}" in text:
        raise RuntimeError("post hook contains doubled braces")
    return text


HONESTY = "The tool failed. Tell the customer it was not recorded. Do not say it succeeded."


def generic_post_hook():
    return what_to_do_next(HONESTY)


def verify_identity_post_hook():
    text = """
try:
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        hint = str(data.get("hint") or "")
        # wrong_answers_left can be int 0 — do not use `or ""` (0 is falsy).
        left_raw = data.get("wrong_answers_left")
        left = "" if left_raw is None else str(left_raw)
        verified = str(data.get("verified") or "").lower() == "true" or data.get("verified") is True
        all_ok = str(data.get("all_verified") or "").lower() == "true" or data.get("all_verified") is True
        if all_ok:
            vars["identity_verified"] = "true"
            data["what_to_do_next"] = "Identity is verified. Call get_account before you say anything about the account."
        elif hint == "day_month_swapped":
            data["what_to_do_next"] = "Ask them to say the month as a word. This retry is not a wrong answer."
        elif left in ("0", "0.0"):
            vars["forced_outcome"] = "not_verified"
            vars["forced_reason"] = "failed"
            vars["resolved_outcome"] = "not_verified"
            vars["resolved_reason"] = "failed"
            data["what_to_do_next"] = "Verification failed. Do not state the debt. Tell them to use the mobile app or a branch, then say goodbye."
        elif not verified:
            data["what_to_do_next"] = "That answer was not accepted. Ask the same question again. Do not state the debt."
        else:
            data["what_to_do_next"] = "Ask the next security question. Do not state the debt yet."
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("verify post hook braces")
    return text


def get_account_post_hook():
    text = """
import json
try:
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        products = data.get("products")
        if isinstance(products, list):
            vars["account_products_json"] = json.dumps(products)
        ok = data.get("ok") is True or str(data.get("ok")).lower() == "true"
        if ok:
            data["what_to_do_next"] = "Account loaded. State every late product from account_summary, then ask if they can pay today. Do not invent figures."
        else:
            data["what_to_do_next"] = "The account could not be read. Do not invent a balance."
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("account post hook braces")
    return text


def create_promise_post_hook():
    text = """
try:
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        if str(vars.get("write_blocked") or "") == "yes":
            data["ok"] = False
            reason = str(vars.get("block_reason") or "")
            offer = str(vars.get("offer_date") or "")
            if reason == "after_deadline" and offer:
                due = ""
                try:
                    import json as _json
                    dues = vars.get("past_dues_json") or {}
                    if isinstance(dues, str):
                        dues = _json.loads(dues)
                    pid = str(vars.get("last_promise_pid") or "")
                    if isinstance(dues, dict) and pid in dues:
                        due = str(dues.get(pid))
                except Exception:
                    due = ""
                amt_line = (" full amount " + due) if due else " the full past-due amount"
                data["what_to_do_next"] = (
                    "That date is after the deadline. Do not say a promise was recorded. "
                    "Offer deadline " + offer + " with" + amt_line + " (not the floor). "
                    "Ask them to confirm. Their late date is not a refusal."
                )
            elif reason == "below_floor":
                data["what_to_do_next"] = (
                    "That amount is below the floor. Do not say it was recorded. "
                    "Offer the floor amount once."
                )
            elif reason in ("not_business_day", "holiday"):
                if offer:
                    data["what_to_do_next"] = (
                        "That date is a weekend or holiday. Do not say it was recorded. "
                        "Offer " + offer + " (the next business day). Do NOT jump to the band deadline."
                    )
                else:
                    data["what_to_do_next"] = (
                        "That date is not a business day. Do not say it was recorded. "
                        "Offer the next business day before the deadline."
                    )
            else:
                data["what_to_do_next"] = (
                    "The promise was NOT recorded. Do not say it was recorded. "
                    "Propose a valid business-day date on or before the product deadline."
                )
        else:
            ok = data.get("ok") is True or str(data.get("ok")).lower() == "true"
            if ok:
                vars["has_promise"] = "true"
                vars["promise_recorded"] = "true"
                # One-shot flag so collection does not re-enter promise_recorded on a stale ok.
                vars["promise_just_ok"] = "yes"
                vars["create_promise_failures"] = 0
                if str(vars.get("floor_offered") or "") == "yes":
                    vars["floor_offered"] = "done"
                pid = str(vars.get("last_promise_pid") or "")
                promised = vars.get("promised_products")
                if not isinstance(promised, list):
                    promised = []
                if pid and pid not in promised:
                    promised.append(pid)
                # Persist as JSON string — Freya drops bare list mutations across turns.
                try:
                    import json as _json
                    vars["promised_products"] = _json.dumps(promised)
                except Exception:
                    vars["promised_products"] = promised
                # Do not speak "recorded" here — promise_recorded node TTS does that.
                open_left = []
                try:
                    import json as _json
                    ids = vars.get("allowed_product_ids") or []
                    if isinstance(ids, str):
                        ids = _json.loads(ids)
                    for item in ids or []:
                        if str(item) not in promised:
                            open_left.append(str(item))
                except Exception:
                    open_left = []
                if open_left:
                    data["what_to_do_next"] = (
                        "Promise ok for " + pid + ". Do not say recorded yourself. "
                        "Continue with remaining products: " + ",".join(open_left) + "."
                    )
                else:
                    data["what_to_do_next"] = (
                        "Promise ok. Do not say recorded yourself; the next node will confirm it."
                    )
            else:
                fails = 0
                try:
                    fails = int(float(vars.get("create_promise_failures") or 0))
                except Exception:
                    fails = 0
                fails = fails + 1
                vars["create_promise_failures"] = fails
                if fails >= 2:
                    vars["forced_outcome"] = "no_promise"
                    vars["forced_reason"] = "service_error"
                    vars["resolved_outcome"] = "no_promise"
                    vars["resolved_reason"] = "service_error"
                    data["what_to_do_next"] = (
                        "The promise was NOT recorded after two attempts. "
                        "Do not say it was recorded. Apologize for a service error and say goodbye."
                    )
                else:
                    data["what_to_do_next"] = (
                        "The promise was NOT recorded. Do not say it was recorded. "
                        "Call create_promise once more with the same product, amount and date."
                    )
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("promise post hook braces")
    return text


def add_note_post_hook():
    # note_just_ok is a one-shot (like promise_just_ok). Sticky add_note_response.data.ok
    # otherwise re-exits finalize_notes forever and the model loops on goodbye.
    # note_pid_stack is FIFO from pre_process so parallel add_note calls do not both
    # attribute the last last_note_pid.
    text = """
try:
    import json as _json
    def _pop_note_pid():
        raw = vars.get("note_pid_stack") or "[]"
        if isinstance(raw, str):
            try:
                stack = _json.loads(raw)
            except Exception:
                stack = []
        elif isinstance(raw, list):
            stack = raw
        else:
            stack = []
        if not isinstance(stack, list):
            stack = []
        pid = ""
        if stack:
            pid = str(stack.pop(0) or "")
        try:
            vars["note_pid_stack"] = _json.dumps(stack)
        except Exception:
            vars["note_pid_stack"] = stack
        if not pid:
            pid = str(vars.get("last_note_pid") or "")
        if not pid:
            pid = str(vars.get("add_note_args.product_id") or "")
        return pid
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        if str(vars.get("write_blocked") or "") == "yes":
            data["ok"] = False
            if str(vars.get("block_reason") or "") == "after_deadline_not_refusal":
                offer = str(vars.get("offer_date") or "")
                data["what_to_do_next"] = (
                    "Do not record a refusal. Their date is after the deadline, which is not a refusal. "
                    "Offer deadline " + offer + " with the full past-due amount and ask them to confirm."
                )
            elif str(vars.get("block_reason") or "") == "notes_not_ready":
                data["what_to_do_next"] = (
                    "Do not record a refused note yet. Finish persuasion first; notes are written later."
                )
            elif str(vars.get("block_reason") or "") == "already_noted":
                _pop_note_pid()
                # Progress finalize_notes once; do not leave block_reason sticky as the route.
                vars["note_just_ok"] = "yes"
                data["what_to_do_next"] = (
                    "That product already has a note. Call add_note only for remaining open products."
                )
            else:
                _pop_note_pid()
                data["what_to_do_next"] = "The note was not recorded. Continue without claiming it was."
        else:
            ok = data.get("ok") is True or str(data.get("ok")).lower() == "true"
            if ok:
                notes = vars.get("ledger_notes")
                if isinstance(notes, str) and notes.strip():
                    try:
                        notes = _json.loads(notes)
                    except Exception:
                        notes = []
                if not isinstance(notes, list):
                    notes = []
                pid = _pop_note_pid()
                if not pid:
                    try:
                        pid = str((result.get("data") or {}).get("product_id") or "")
                    except Exception:
                        pid = ""
                if pid and not any(
                    isinstance(row, dict) and str(row.get("product_id") or "") == pid for row in notes
                ):
                    notes.append({"product_id": pid, "reason": str(vars.get("last_note_reason") or "refused")})
                noted = vars.get("noted_products") or []
                if isinstance(noted, str) and noted.strip():
                    try:
                        noted = _json.loads(noted)
                    except Exception:
                        noted = []
                if not isinstance(noted, list):
                    noted = []
                if pid and pid in [str(x) for x in noted]:
                    # Two ok results claimed the same pid: parallel pre-hooks
                    # shared a snapshot and the FIFO stack misattributed one
                    # call. Flag it so check_remaining can credit the
                    # attempted-but-unnoted product from race-safe markers.
                    vars["note_race"] = "yes"
                if pid and pid not in [str(x) for x in noted]:
                    noted.append(pid)
                try:
                    vars["ledger_notes"] = _json.dumps(notes)
                    vars["noted_products"] = _json.dumps(noted)
                except Exception:
                    vars["ledger_notes"] = notes
                    vars["noted_products"] = noted
                vars["note_just_ok"] = "yes"
                data["what_to_do_next"] = (
                    "Note recorded for "
                    + pid
                    + ". If open products remain, CALL add_note once for the next one only."
                )
            else:
                _pop_note_pid()
                data["what_to_do_next"] = "The note was not recorded."
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("note post hook braces")
    return text


def schedule_callback_post_hook():
    text = """
try:
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        if str(vars.get("write_blocked") or "") == "yes":
            data["ok"] = False
            allowed = str(vars.get("callback_allowed_dates") or "")
            tomorrow = str(vars.get("callback_tomorrow") or "")
            data["what_to_do_next"] = (
                "That callback time is not allowed. Ask once for an allowed date ("
                + allowed
                + "), time 10:00-19:00. If they said tomorrow, use "
                + tomorrow
                + " at 11:00. If still outside, do not schedule; say goodbye."
            )
        else:
            ok = data.get("ok") is True or str(data.get("ok")).lower() == "true"
            if ok:
                vars["has_callback"] = "true"
                vars["forced_outcome"] = "not_reached"
                vars["forced_reason"] = "callback"
                vars["resolved_outcome"] = "not_reached"
                vars["resolved_reason"] = "callback"
                data["what_to_do_next"] = (
                    "Callback is booked. Thank them and say goodbye ending with Have a good day."
                )
            else:
                data["what_to_do_next"] = (
                    "The callback was not booked. Ask once for another allowed time, or say goodbye."
                )
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("callback post hook braces")
    return text


def transfer_post_hook():
    text = """
try:
    if isinstance(result, dict):
        result.pop("header", None)
        data = result.get("data")
        if not isinstance(data, dict):
            data = {}
            result["data"] = data
        if str(vars.get("write_blocked") or "") == "yes":
            data["ok"] = False
            data["what_to_do_next"] = "Transfer was blocked. Continue without claiming a transfer."
        else:
            ok = data.get("ok") is True or str(data.get("ok")).lower() == "true"
            if ok:
                vars["ledger_transferred"] = "true"
                queue = str(vars.get("transfer_queue") or "")
                if not queue:
                    queue = "human"
                vars["transfer_queue"] = queue
                vars["forced_outcome"] = "transferred"
                vars["forced_reason"] = queue
                vars["resolved_outcome"] = "transferred"
                vars["resolved_reason"] = queue
                data["what_to_do_next"] = "Transfer succeeded. Say a short handoff and goodbye."
            else:
                data["what_to_do_next"] = "Transfer failed. Do not claim it succeeded."
except Exception:
    pass
"""
    if "{{" in text or "}}" in text:
        raise RuntimeError("transfer post hook braces")
    return text
