"""Compute close_call outcome and reason from ledger state."""
import json


def _notes(value):
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


def _on(value):
    return str(value).lower() == "true"


def resolve_outcome(state):
    """Map a vars dict to (outcome, reason or None)."""
    state = state or {}
    if _on(state.get("ledger_transferred")):
        return "transferred", str(state.get("transfer_queue") or "human")
    if _on(state.get("has_promise")):
        return "promise", None
    notes = _notes(state.get("ledger_notes"))
    if notes:
        first = ""
        all_paid = True
        for note in notes:
            reason = str((note or {}).get("reason") or "")
            if reason != "paid":
                all_paid = False
                if not first:
                    first = reason
        if all_paid:
            return "paid", None
        return "no_promise", first or "refused"
    if _on(state.get("has_callback")):
        return "not_reached", "callback"
    forced = str(state.get("forced_outcome") or state.get("resolved_outcome") or "")
    if forced:
        reason = state.get("forced_reason")
        if reason in (None, "", "none", "None"):
            reason = None
        if forced in ("promise", "paid"):
            reason = None
        return forced, reason
    return "no_promise", "refused"


def apply_outcome(vars):
    outcome, reason = resolve_outcome(vars)
    vars["resolved_outcome"] = outcome
    vars["resolved_reason"] = "" if reason is None else str(reason)


OUTCOME_RESOLVER_CODE = """
import json
try:
    def _notes(value):
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
    def _on(value):
        return str(value).lower() == "true"
    if _on(vars.get("ledger_transferred")):
        vars["resolved_outcome"] = "transferred"
        vars["resolved_reason"] = str(vars.get("transfer_queue") or "human")
    elif _on(vars.get("has_promise")):
        vars["resolved_outcome"] = "promise"
        vars["resolved_reason"] = ""
    else:
        notes = _notes(vars.get("ledger_notes"))
        if notes:
            first = ""
            all_paid = True
            for note in notes:
                reason = str((note or {}).get("reason") or "")
                if reason != "paid":
                    all_paid = False
                    if not first:
                        first = reason
            if all_paid:
                vars["resolved_outcome"] = "paid"
                vars["resolved_reason"] = ""
            else:
                vars["resolved_outcome"] = "no_promise"
                vars["resolved_reason"] = first or "refused"
        elif _on(vars.get("has_callback")):
            vars["resolved_outcome"] = "not_reached"
            vars["resolved_reason"] = "callback"
        else:
            cb_ok = vars.get("schedule_callback_response.data.ok")
            if cb_ok is None:
                resp = vars.get("schedule_callback_response")
                if isinstance(resp, dict):
                    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                    cb_ok = data.get("ok")
            if cb_ok is True or str(cb_ok).lower() == "true":
                vars["has_callback"] = "true"
                vars["resolved_outcome"] = "not_reached"
                vars["resolved_reason"] = "callback"
            elif vars.get("forced_outcome"):
                vars["resolved_outcome"] = str(vars.get("forced_outcome"))
                reason = str(vars.get("forced_reason") or "")
                if vars["resolved_outcome"] in ("promise", "paid") or reason in ("", "none", "None"):
                    reason = ""
                vars["resolved_reason"] = reason
            elif not vars.get("resolved_outcome"):
                vars["resolved_outcome"] = "no_promise"
                vars["resolved_reason"] = "refused"
except Exception:
    if not vars.get("resolved_outcome"):
        vars["resolved_outcome"] = "no_promise"
        vars["resolved_reason"] = "refused"
"""

if "{{" in OUTCOME_RESOLVER_CODE or "}}" in OUTCOME_RESOLVER_CODE:
    raise RuntimeError("outcome snippet has doubled braces")


def resolve(products, promises, notes, transfers, explicit_outcome, explicit_reason, notes_enabled):
    if transfers:
        reason = str((transfers[0] or {}).get("reason") or "")
        return "transferred", reason
    recorded = []
    for row in promises or []:
        if str(row.get("ok")) == "yes":
            recorded.append(row)
    if recorded:
        return "promise", ""
    if notes_enabled and notes:
        first = ""
        all_paid = True
        for note in notes:
            reason = str(note.get("reason") or "")
            if reason != "paid":
                all_paid = False
                if not first:
                    first = reason
        if all_paid:
            return "paid", ""
        return "no_promise", first or "refused"
    if explicit_outcome:
        return str(explicit_outcome), str(explicit_reason or "")
    for status in ("hardship", "dispute", "paid", "service_error", "refused"):
        for product in products or []:
            if str(product.get("status")) == status:
                if status == "paid":
                    return "paid", ""
                if status == "service_error":
                    return "no_promise", "service_error"
                return "no_promise", status
    return "no_promise", "refused"
