"""Extract a case policy IR from a case folder with one logged model call.

The model reads every source document (brief, analysis, prompt sheets, meeting notes, mails,
service contract, flow drawing text) and fills the generic schema in case_schema.json.
Nothing bank-specific lives here: wording, limits, field names and currency names all come
from the pack. The result is validated structurally; on failure the model is asked once more
with the validation errors.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = json.load(open(os.path.join(HERE, "case_schema.json"), encoding="utf-8"))

DEFAULT_MODEL = os.environ.get("BUILDER_LLM", "")  # empty = claude CLI default


def _sources_text(case: Dict[str, Any], max_chars: int = 400_000) -> str:
    parts = []
    if case.get("brief"):
        parts.append(f"===== FILE: {case['brief']['path']} =====\n{case['brief']['text']}")
    for s in case.get("sources", []):
        if case.get("brief") and s["path"] == case["brief"]["path"]:
            continue
        parts.append(f"===== FILE: {s['path']} =====\n{s['text']}")
    for p in case.get("pdfs", []):
        from ingest_case import pdf_text  # type: ignore
        txt = pdf_text(p)
        if txt.strip():
            parts.append(f"===== FILE: {os.path.relpath(p, case['folder'])} (text extracted from PDF) =====\n{txt}")
    text = "\n\n".join(parts)
    return text[:max_chars]


def build_prompt(case: Dict[str, Any], services_ir: Dict[str, Any], errors: Optional[List[str]] = None) -> str:
    svc_summary = {
        n: {
            "url": s["url"],
            "request_data_fields": s["request_fields"],
            "request_data_example": s["request_data_example"],
            "response_data_example": s["response_data_example"],
            "errors": [{"http": e["http"], "code": e["code"], "type": e.get("type", "")} for e in s["errors"]],
        }
        for n, s in services_ir["services"].items()
    }
    schema = SCHEMA
    instructions = f"""
You are extracting a machine-readable policy for a bank's voice-assistant step from raw project material.
Everything you output must come from the material below (newest document wins on conflicts: recap mails and
the latest meeting override earlier analysis drafts, legacy prompt sheets and public FAQ pages). Where the
material gives verbatim sentences in the call language, copy them EXACTLY (keep diacritics). Where no verbatim
sentence exists, write a short natural sentence in the call language consistent with the decided behaviour.

Output: ONE JSON object and nothing else (no markdown fences, no commentary). Shape:

{{
  "schema_version": "case-1",
  "language": "<BCP47 language of the calls, e.g. tr>",
  "assistant_role": "<one sentence, in the call language, describing who the assistant is and what it does in this step>",
  "bank_name": "<bank name as the caller would hear it>",
  "call_variables": {{"customer_id": "<name of the call variable carrying the customer id>", "now": "<name of the call variable carrying the call time>"}},
  "service_roles": {{ {", ".join(f'"{r}": "<service name>"' for r in schema["service_roles"])} }},
  "request_fields": {{ "<service name>": {{ "<data field>": "<value>" }} }},   // value = a constant from the contract, or one of these logical placeholders exactly: {{customer_id}}, {{card_number}}, {{new_week}}, {{current_day}}, {{statement_date}}, {{email}}
  "response_fields": {{
     "list_accounts": {{"items_path": "<field holding the array of accounts/cards>", "item_number": "<field>", "item_last4": "<field>", "item_name": "<field>", "item_main_flag": "<field or empty>", "no_account_http": <http status meaning 'no eligible account'>}},
     "balance": {{"statement_date": "<field>", "due_date": "<field>", "next_statement_date": "<field>", "next_due_date": "<field>", "currency_blocks": {{"<block field>": "<currency code>"}}, "statement_debt": "<field in block>", "min_payment": "<field>", "remaining_debt": "<field>", "remaining_min_payment": "<field>", "total_debt": "<field>", "status_field": "<field in block>", "status_no_debt": "<value>", "auto_payment_flag": "<top-level field>", "auto_payment_flag_yes": "<value>", "auto_payment_amount": "<field in block>", "overall_status_field": "<top-level field>"}},
     "cycle_get": {{"week": "<field>", "day": "<field>", "statement_date": "<field>", "due_date": "<field>", "next_statement_date": "<field>", "next_due_date": "<field>", "relation_type": "<field>", "relation_main_value": "<value>", "relation_linked_value": "<value>", "related_number": "<field>", "related_name": "<field>", "week_min": 1, "week_max": 4}},
     "cycle_preview": {{"statement_date": "<field>", "due_date": "<field>", "day": "<field>"}},
     "cycle_change": {{"success_field": "<field>", "success_value": "<value>"}},
     "contact_get": {{"email": "<field>", "tts_email": "<field or empty>", "type_field": "<field>", "type_no_email_value": "<value>"}},
     "document_send": {{"success_field": "<field>", "success_value": "<value>", "currency_value": "<value to send in the currency field>", "statement_date_source": "balance.statement_date"}}
  }},
  "currencies": [ {{"code": "<code>", "block": "<balance block field>", "spoken": "<full spoken name>", "minor_unit": "<spoken name of the hundredth unit>", "is_domestic": true|false}} ... in read-out order ],
  "readout": {{
     "order": ["intro", "domestic", "foreign", "no_debt", "auto_payment", "due_date"],
     "zero_or_negative_is_no_debt": true,
     "missing_block_in_no_debt_sentence": true|false,
     "domestic_branch_rule": "<how to choose between tl_unpaid / tl_min_paid / tl_partial, in words>",
     "amount_style": "words",
     "drop_zero_minor": true,
     "digits_spoken_one_by_one": true,
     "date_style": "day monthname year",
     "offer_after_no_statement_debt": true|false,
     "ask_another_card_before_offer": true
  }},
  "phrases": {{ "<key>": "<sentence with {{placeholders}}>" }},
  "limits": {{"attempts_per_question": <int>, "silence_repeats_before_end": <int>, "complaint_replies_before_transfer": <int>, "document_resends_max": <int>}},
  "handling": {{"payment": "...", "dispute": "...", "agent_request": "...", "service_error": "...", "unsupported_language": "...", "silence": "...", "complaint": "...", "out_of_scope": "...", "third_party": "...", "no_account": "...", "contact_service_error": "..."}},
  "cycle_change_limit": {{"per": "calendar_year", "count": <int>, "offer_when_unknown": true}},
  "never_say": ["<things the assistant must never read out, in words>"],
  "notes": ["<short notes on conflicts you resolved and which source won>"]
}}

Rules for "phrases" (keys listed below). Use ONLY the given placeholder names, in curly braces, e.g. {{card_name}}.
Do not attach grammatical suffixes directly to a placeholder (write the sentence so the placeholder ends a clause or
is followed by a separate word), because values are inserted verbatim. Placeholders:
{json.dumps(schema["placeholders"], ensure_ascii=False, indent=1)}
Required phrase keys: {json.dumps(schema["phrases_required"])}
Optional phrase keys (include when the material supports them): {json.dumps(schema["phrases_optional"])}
Meaning of keys: no_eligible_card (customer has no usable card; then main menu); ask_which_card (several cards, with two
example names); ask_which_card_short (same without examples, for repeats); ask_last4 / ask_last6 / ask_full_number
(duplicate-name disambiguation steps in that order); card_not_found (named card not in list; {{card_list}} = names);
another_card (ask about another card); debt_intro (prefix sentence fragment naming the card and statement date, to be
followed directly by the first currency sentence); debt_intro_no_name (same when the product name is empty);
tl_unpaid / tl_min_paid / tl_partial (domestic currency sentences for: nothing paid / minimum or more paid / less than
minimum paid; {{currency}} is the spoken currency name and amounts are already spoken amounts); fx_debt (foreign currency
sentence); no_debt_currencies (one sentence listing currencies without debt); auto_payment (remaining automatic payment);
due_date / due_today; no_statement_debt (card has no statement debt: total debt incl. future periods and due date);
no_statement_yet (no statement issued yet); credit_balance (optional: the card has a credit / negative total balance of {{amount}} in the caller's favour); offer_both / offer_change_only / offer_email_only (follow-up offer variants);
email_confirm (read registered address, ask to send); email_sent; no_email (no registered address); document_unavailable;
cycle_current (current statement week and next dates, then ask whether to change); cycle_current_shared (same for a
shared statement); cycle_options ({{options}} = allowed weeks as ordinal words); cycle_shared_impact (disclosure that the
linked card is affected, said BEFORE the confirmation question); cycle_confirm (preview dates + ask for confirmation);
cycle_done (success); cycle_fail_transfer; cycle_already_used; cycle_no_change_needed; cycle_option_invalid;
service_error_transfer; payment_transfer; dispute_transfer; agent_transfer; to_menu (normal hand-back to the menu);
failed_attempts_exit; silence_exit; other_language_exit; out_of_scope_ask; checking (said while a slow service answers);
third_party_exit; ask_clear_yes (ask again for a clear yes/no); only_last_statement; email_other_address;
calm_reply_hint (one short calming sentence for an upset caller).

Handling values: one of {json.dumps(schema["handling_values"])}. "menu" = say the hand-back sentence and end the step;
"transfer" = say the transfer sentence and end the step (the platform hands over); "end_call" = end without menu;
"ask_menu" = ask whether to go to the menu, otherwise resume; "calm_then_transfer" = calming replies up to the limit then transfer.

The service contract was parsed as (use these exact field names):
{json.dumps(svc_summary, ensure_ascii=False, indent=1)}
"""
    if errors:
        instructions += "\n\nYour previous answer had these validation problems; fix them all:\n- " + "\n- ".join(errors)
    return instructions + "\n\n===== MATERIAL =====\n" + _sources_text(case)


def validate_policy(p: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    s = SCHEMA
    for k in s["required_top"]:
        if k not in p:
            errs.append(f"missing top-level key {k}")
    roles = p.get("service_roles") or {}
    for r in s["service_roles"]:
        if not roles.get(r):
            errs.append(f"service_roles.{r} missing")
    rf = p.get("response_fields") or {}
    for role, keys in s["response_fields_required"].items():
        block = rf.get(role) or {}
        for k in keys:
            if k not in block or block[k] in (None, ""):
                errs.append(f"response_fields.{role}.{k} missing")
    if not (rf.get("balance") or {}).get("currency_blocks"):
        errs.append("response_fields.balance.currency_blocks missing")
    ph = p.get("phrases") or {}
    for k in s["phrases_required"]:
        if not isinstance(ph.get(k), str) or not ph.get(k).strip():
            errs.append(f"phrases.{k} missing")
    for k, allowed in s["placeholders"].items():
        t = ph.get(k)
        if isinstance(t, str):
            used = set(re.findall(r"\{(\w+)\}", t))
            bad = used - set(allowed)
            if bad:
                errs.append(f"phrases.{k} uses unknown placeholders {sorted(bad)}; allowed {allowed}")
    for k in ("ask_which_card",):
        t = ph.get(k)
        if isinstance(t, str) and ("{example1}" not in t or "{example2}" not in t):
            errs.append("phrases.ask_which_card must contain {example1} and {example2}")
    lim = p.get("limits") or {}
    for k in s["limits_required"]:
        if not isinstance(lim.get(k), int) or lim.get(k) < 1:
            errs.append(f"limits.{k} must be a positive integer")
    h = p.get("handling") or {}
    for k in s["handling_required"]:
        if h.get(k) not in s["handling_values"]:
            errs.append(f"handling.{k} must be one of {s['handling_values']}")
    cur = p.get("currencies") or []
    if not isinstance(cur, list) or not cur:
        errs.append("currencies must be a non-empty list")
    else:
        for c in cur:
            for k in ("code", "block", "spoken", "minor_unit"):
                if not c.get(k):
                    errs.append(f"currency {c} missing {k}")
        if not any(c.get("is_domestic") for c in cur):
            errs.append("one currency must have is_domestic true")
    return errs


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(m.group(0).replace("“", '"').replace("”", '"'))
        except Exception:
            return None


def call_model(prompt: str, model: str = DEFAULT_MODEL, timeout_s: int = 900) -> Tuple[str, str, Dict[str, Any]]:
    """Run the claude CLI non-interactively. Returns (text, model_name, meta)."""
    cmd = ["claude", "-p", "--output-format", "json", "--tools", ""]
    if model:
        cmd += ["--model", model]
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    t0 = time.time()
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout_s, env=env)
    out = proc.stdout
    meta: Dict[str, Any] = {"returncode": proc.returncode, "seconds": round(time.time() - t0, 1)}
    model_name = model or "claude-cli-default"
    text = out
    try:
        j = json.loads(out)
        text = j.get("result", "")
        mu = j.get("modelUsage") or {}
        if isinstance(mu, dict) and mu:
            model_name = sorted(mu.keys(), key=lambda k: -(mu[k].get("outputTokens", 0) if isinstance(mu[k], dict) else 0))[0]
        meta["usage"] = j.get("usage")
        meta["cost_usd"] = j.get("total_cost_usd")
        meta["session_id"] = j.get("session_id")
    except Exception:
        meta["stderr"] = proc.stderr[-2000:]
    return text, model_name, meta


def extract_case_policy(case: Dict[str, Any], services_ir: Dict[str, Any], trace=None, attempts: int = 2, model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    errors: Optional[List[str]] = None
    last: Dict[str, Any] = {}
    for i in range(attempts):
        prompt = build_prompt(case, services_ir, errors)
        text, model_name, meta = call_model(prompt, model=model)
        if trace is not None:
            trace.model_call(model_name, f"extract case policy from source material (attempt {i + 1})", **{k: v for k, v in meta.items() if k != "stderr"})
        pol = _parse_json(text)
        if pol is None:
            errors = ["answer was not a single JSON object"]
            last = {"_raw": text[-4000:], "_meta": meta}
            continue
        errors = validate_policy(pol)
        pol["_extraction"] = {"model": model_name, "attempt": i + 1, **{k: v for k, v in meta.items() if k != "stderr"}, "validation_errors": errors}
        last = pol
        if not errors:
            return pol
    return last


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    from ingest_case import ingest_case  # type: ignore
    from contract import parse_contract_markdown  # type: ignore
    case = ingest_case(sys.argv[1])
    ir = parse_contract_markdown(case["contract"]["text"])
    if len(sys.argv) > 2 and sys.argv[2] == "--prompt":
        print(build_prompt(case, ir))
        sys.exit(0)
    pol = extract_case_policy(case, ir)
    out = sys.argv[2] if len(sys.argv) > 2 else "out/case.policy.json"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(pol, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("validation errors:", pol.get("_extraction", {}).get("validation_errors") or pol.get("_raw", "")[:500])
