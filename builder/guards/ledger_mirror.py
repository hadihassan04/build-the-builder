"""Call-local mirror of promises, notes, transfers, and callbacks."""
import json


def load_json(vars, key):
    raw = vars.get(key)
    if isinstance(raw, list):
        return list(raw)
    if raw in (None, ""):
        return []
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def save_json(vars, key, value):
    vars[key] = json.dumps(value)


def add_item(vars, key, item):
    rows = load_json(vars, key)
    rows.append(item)
    save_json(vars, key, rows)
    return rows


def has_product(rows, product_id):
    for row in rows:
        if str(row.get("product_id")) == str(product_id):
            return True
    return False


def _ok(result):
    data = {}
    if isinstance(result, dict):
        inner = result.get("data")
        if isinstance(inner, dict):
            data = inner
    flag = data.get("ok")
    return flag is True or str(flag).lower() == "true"


def update_ledger_from_tool(vars, tool, result, args=None):
    args = args or {}
    if not _ok(result):
        return vars
    if tool == "create_promise":
        vars["has_promise"] = "true"
        vars["promise_recorded"] = "true"
        promised = vars.get("promised_products")
        if not isinstance(promised, list):
            promised = []
        pid = str(args.get("product_id") or "")
        if pid and pid not in promised:
            promised.append(pid)
        vars["promised_products"] = promised
        rows = load_json(vars, "ledger_promises")
        rows.append({"product_id": pid, "amount": args.get("amount"), "date": args.get("date"), "ok": "yes"})
        save_json(vars, "ledger_promises", rows)
    elif tool == "add_note":
        vars.setdefault("ledger_notes", [])
        if not isinstance(vars.get("ledger_notes"), list):
            vars["ledger_notes"] = load_json(vars, "ledger_notes")
        vars["ledger_notes"].append({"product_id": str(args.get("product_id") or ""), "reason": str(args.get("reason") or "")})
    elif tool == "schedule_callback":
        vars["has_callback"] = "true"
        add_item(vars, "ledger_callbacks", {"date": str(args.get("date") or ""), "time": str(args.get("time") or "")})
    elif tool == "transfer_to_agent":
        vars["ledger_transferred"] = "true"
        queue = str(args.get("queue") or "")
        reason = "human" if queue == "general" else queue
        vars["transfer_queue"] = reason
        add_item(vars, "ledger_transfers", {"queue": queue, "reason": reason})
    return vars
