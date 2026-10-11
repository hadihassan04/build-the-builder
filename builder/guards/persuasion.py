"""Per-product persuasion attempt counters (Brief A rule 30 / Brief B rule 5)."""
from __future__ import annotations

import json


def _load_map(raw):
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


def _load_list(raw):
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


def _open_product_ids(vars):
    """Open products in get_account order (not promised, not noted)."""
    ids = _load_list(vars.get("allowed_product_ids"))
    promised = [str(x) for x in _load_list(vars.get("promised_products"))]
    noted = []
    for row in _load_list(vars.get("ledger_notes")):
        if isinstance(row, dict) and row.get("product_id"):
            noted.append(str(row.get("product_id")))
    open_ids = []
    for pid in ids:
        sp = str(pid)
        if sp in promised or sp in noted:
            continue
        open_ids.append(sp)
    return open_ids


def advance_persuasion(vars):
    """On flat refusal: offer the next attempt for ONE product (Brief B one-by-one).

    persuasion_used[pid] = number of rule-30 persuasion offers already spoken.
    Max attempts come from attempts_json (band). Opening "pay today?" is not counted.
    """
    vars["need_fit_dates"] = "no"
    vars["resume_collection"] = "no"
    intent = str(vars.get("customer_intent") or "")
    proposed = str(vars.get("proposed_date") or "").strip()
    consumed = str(vars.get("proposed_date_consumed") or "") == "yes"
    # Same-turn named date with proposes_date / vague pay intent → fit first.
    # On refuses/confirm_no, ignore stale proposed_date (extraction often leaves a past day).
    if proposed and (not consumed) and intent not in ("refuses", "confirm_no", "no_talk"):
        vars["continue_persuasion"] = "no"
        vars["awaiting_notes"] = "no"
        vars["need_fit_dates"] = "yes"
        vars["persuasion_focus"] = ""
        vars["persuasion_tts"] = ""
        vars["persuasion_instruction"] = (
            "Customer named payment date "
            + proposed
            + ". Fit dates and create_promise for payable products first. Do not add refused notes yet."
        )
        return
    if proposed and intent in ("refuses", "confirm_no", "no_talk"):
        # Do not assign proposed_date="" — Freya date schema rejects empty strings.
        vars["proposed_date_consumed"] = "yes"

    # Deadline was offered and refused — clamped fitted dates are not payables.
    if str(vars.get("deadline_refused") or "") == "yes":
        vars["fitted_dates_json"] = "{}"
        vars["gate_ok"] = "no"
        vars["resume_collection"] = "no"

    # Confirm payable fitted products before burning persuasion attempts on refusals.
    # fit_product_dates may stamp every product; if the customer is refusing some,
    # keep only the first unpromised fitted product (get_account order) for confirm.
    fitted = _load_map(vars.get("fitted_dates_json"))
    promised_now = [str(x) for x in _load_list(vars.get("promised_products"))]
    id_order = [str(x) for x in _load_list(vars.get("allowed_product_ids"))]
    pending_pay = []
    for pid in id_order:
        if pid in promised_now:
            continue
        if str(fitted.get(pid) or "").strip():
            pending_pay.append(pid)
    if (
        pending_pay
        and intent in ("refuses", "confirm_no", "no_talk")
        and str(vars.get("deadline_refused") or "") != "yes"
    ):
        if promised_now:
            # Promises already recorded — leftover multi-fit stamps are not payables.
            kept = {}
            for pid, day in fitted.items():
                if str(pid) in promised_now and str(day or "").strip():
                    kept[str(pid)] = str(day)
            vars["fitted_dates_json"] = json.dumps(kept)
            # Fall through to per-product persuasion on the true open set.
        else:
            keep = pending_pay[0]
            trimmed = {}
            for pid, day in fitted.items():
                sp = str(pid)
                if sp == keep and str(day or "").strip():
                    trimmed[sp] = str(day)
            vars["fitted_dates_json"] = json.dumps(trimmed)
            vars["continue_persuasion"] = "no"
            vars["resume_collection"] = "yes"
            vars["awaiting_notes"] = "no"
            vars["need_fit_dates"] = "no"
            vars["persuasion_focus"] = ""
            vars["persuasion_tts"] = ""
            vars["persuasion_instruction"] = (
                "Customer will pay "
                + keep
                + " on fitted date "
                + str(trimmed.get(keep) or "")
                + ". Confirm and CALL create_promise for that product first. "
                "Do not persuade or add_note other products until that promise succeeds."
            )
            return

    open_ids = _open_product_ids(vars)
    attempts = _load_map(vars.get("attempts_json"))
    used = _load_map(vars.get("persuasion_used_json"))
    labels = _load_map(vars.get("labels_json"))
    dues = _load_map(vars.get("past_dues_json"))
    floors = _load_map(vars.get("floors_json"))
    deadlines = _load_map(vars.get("deadlines_json"))
    a1 = str(vars.get("attempt1_date") or "")
    a1_spoken = str(vars.get("attempt1_spoken") or a1)
    full_too_much = str(vars.get("full_too_much") or "").lower() == "true"

    vars["open_products"] = ",".join(open_ids)

    if not open_ids:
        vars["continue_persuasion"] = "no"
        vars["awaiting_notes"] = "no"
        vars["persuasion_instruction"] = ""
        vars["persuasion_focus"] = ""
        return

    focus = None
    for pid in open_ids:
        max_n = 1
        try:
            max_n = int(attempts.get(pid) or 1)
        except Exception:
            max_n = 1
        if max_n < 1:
            max_n = 1
        cur = 0
        try:
            cur = int(used.get(pid) or 0)
        except Exception:
            cur = 0
        if cur < max_n:
            focus = pid
            break

    if focus is None:
        vars["continue_persuasion"] = "no"
        vars["awaiting_notes"] = "yes"
        vars["persuasion_focus"] = ""
        vars["persuasion_used_json"] = json.dumps(used)
        vars["persuasion_instruction"] = (
            "Persuasion attempts are used up for all open products ("
            + ",".join(open_ids)
            + "). Stop offering. Notes will be recorded next."
        )
        return

    max_n = 1
    try:
        max_n = int(attempts.get(focus) or 1)
    except Exception:
        max_n = 1
    if max_n < 1:
        max_n = 1
    cur = 0
    try:
        cur = int(used.get(focus) or 0)
    except Exception:
        cur = 0
    next_n = cur + 1
    used[focus] = next_n
    label = str(labels.get(focus) or focus)
    due = dues.get(focus)
    floor = floors.get(focus)
    deadline = str(deadlines.get(focus) or "")
    try:
        due_s = "%.2f" % float(due)
    except Exception:
        due_s = str(due or "")
    try:
        floor_s = "%.2f" % float(floor)
    except Exception:
        floor_s = str(floor or "")

    if next_n == 1:
        if full_too_much:
            tts = (
                "For your "
                + label
                + ", can you pay "
                + floor_s
                + " on "
                + a1_spoken
                + "?"
            )
        else:
            tts = (
                "For your "
                + label
                + ", can you pay "
                + due_s
                + " on "
                + a1_spoken
                + "?"
            )
        offer = (
            "Persuasion attempt 1/"
            + str(max_n)
            + " for "
            + label
            + " ("
            + focus
            + "): full/floor on Attempt-1 "
            + a1
            + "."
        )
    else:
        tts = (
            "For your "
            + label
            + ", can you pay the minimum of "
            + floor_s
            + " by "
            + deadline
            + "? The rest would stay overdue."
        )
        offer = (
            "Persuasion attempt "
            + str(next_n)
            + "/"
            + str(max_n)
            + " for "
            + label
            + " ("
            + focus
            + "): floor on deadline "
            + deadline
            + "."
        )

    vars["persuasion_used_json"] = json.dumps(used)
    vars["persuasion_focus"] = focus
    vars["persuasion_tts"] = tts
    vars["continue_persuasion"] = "yes"
    vars["awaiting_notes"] = "no"
    vars["persuasion_instruction"] = (
        "Persuasion offer was spoken by TTS. Do not restate it or invent another offer. "
        + offer
    )


def build_persuasion_code():
    # Embed plain sources (no typing imports) for the Freya code_exec sandbox.
    chunks = [
        "import json",
        "def _load_map(raw):\n"
        "    if isinstance(raw, dict):\n"
        "        return dict(raw)\n"
        "    if isinstance(raw, str) and raw.strip():\n"
        "        try:\n"
        "            parsed = json.loads(raw)\n"
        "        except Exception:\n"
        "            return {}\n"
        "        if isinstance(parsed, dict):\n"
        "            return parsed\n"
        "    return {}\n",
        "def _load_list(raw):\n"
        "    if isinstance(raw, list):\n"
        "        return list(raw)\n"
        "    if isinstance(raw, str) and raw.strip():\n"
        "        try:\n"
        "            parsed = json.loads(raw)\n"
        "        except Exception:\n"
        "            return []\n"
        "        if isinstance(parsed, list):\n"
        "            return parsed\n"
        "    return []\n",
        "def _open_product_ids(vars):\n"
        "    ids = _load_list(vars.get('allowed_product_ids'))\n"
        "    promised = [str(x) for x in _load_list(vars.get('promised_products'))]\n"
        "    noted = []\n"
        "    for row in _load_list(vars.get('ledger_notes')):\n"
        "        if isinstance(row, dict) and row.get('product_id'):\n"
        "            noted.append(str(row.get('product_id')))\n"
        "    open_ids = []\n"
        "    for pid in ids:\n"
        "        sp = str(pid)\n"
        "        if sp in promised or sp in noted:\n"
        "            continue\n"
        "        open_ids.append(sp)\n"
        "    return open_ids\n",
    ]
    import inspect

    # Re-build PERSUASION_CODE from live advance_persuasion source.
    chunks.append(inspect.getsource(advance_persuasion))
    chunks.append(
        "try:\n"
        "    advance_persuasion(vars)\n"
        "except Exception:\n"
        "    vars['continue_persuasion'] = 'no'\n"
        "    vars['awaiting_notes'] = 'yes'\n"
        "    vars['need_fit_dates'] = 'no'\n"
        "    vars['persuasion_instruction'] = ''\n"
    )
    text = "\n".join(chunks)
    if "{{" in text or "}}" in text:
        raise RuntimeError("persuasion code has doubled braces")
    return text


PERSUASION_CODE = build_persuasion_code()
