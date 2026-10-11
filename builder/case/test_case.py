#!/usr/bin/env python3
"""Offline behaviour tests: execute the compiled workflow's code nodes locally with simulated service answers.

    python3 builder/case/test_case.py out/final.workflow.json out/final.policy.json

Every test drives the real node code (the same Python the platform runs) through a tiny engine:
api_request nodes are answered from the case's test data (or a synthetic answer), conversation nodes
get scripted extraction values, TTS/exit texts are asserted. Values asserted here are read from the
policy and the test data so the file stays case-agnostic.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))


class Engine:
    def __init__(self, wf: Dict[str, Any], answers):
        self.wf = wf
        self.nodes = wf["nodes"]
        self.answers = answers  # fn(node_name, body) -> (status, body)
        self.vars: Dict[str, Any] = {}
        self.spoken: List[str] = []
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.ended = False
        self.exit_text = ""

    @staticmethod
    def _sub(s: str, vars: Dict[str, Any]) -> str:
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(vars.get(m.group(1), "")), s)

    def _bake(self, props: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for k, p in props.items():
            if p.get("type") == "object":
                out[k] = self._bake(p.get("properties") or {})
            else:
                out[k] = self._sub(str(p.get("default", "")), self.vars)
        return out

    def _code(self, node) -> None:
        code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        g = {"vars": self.vars}
        exec(code, g)  # noqa: S102 - this is the artefact under test

    def _api(self, node) -> None:
        fn = node["pre_actions"][0]["function"]
        body = self._bake(fn["parameters"]["properties"]["body"]["properties"])
        status, resp_body = self.answers(node["name"], body)
        self.calls.append((node["name"], body))
        name = node["name"]
        # flatten exactly like the platform: dots, list indexes, booleans/nulls as strings
        def flat(prefix, o):
            if isinstance(o, dict):
                for k, v in o.items():
                    flat(prefix + "." + str(k), v)
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    flat(prefix + "." + str(i), v)
            elif isinstance(o, bool):
                self.vars[prefix] = "true" if o else "false"
            elif o is None:
                self.vars[prefix] = "null"
            else:
                self.vars[prefix] = o
        self.vars[name + "_response.status"] = "success" if status == 200 else "error"
        flat(name + "_response.data", resp_body)
        self.vars[name + "_response.status_code"] = status
        self.vars[name + "_status_code"] = status
        if status == 200:
            self.vars[name + "_success_count"] = 1
        else:
            self.vars[name + "_failure_count"] = 1
        for m in node.get("response_mapping", []):
            cur: Any = resp_body
            for part in m["path"].split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is not None:
                self.vars[m["variable"]] = cur

    def _next(self, node) -> Optional[str]:
        for t in node.get("transitions", []):
            c = t.get("condition", {})
            if c.get("operator") == "always":
                return t["target_node"]
            for cc in c.get("conditions", []):
                if cc.get("operator") == "equals" and str(self.vars.get(cc["variable"], "")) == str(cc["operand"]):
                    return t["target_node"]
        return node.get("passthrough_default_transition")

    def run(self, start: str, turns: List[Dict[str, Any]], max_steps: int = 200) -> None:
        """turns: scripted extraction dicts consumed each time a non-passthrough conversation node is reached."""
        cur = start
        steps = 0
        while cur and not self.ended and steps < max_steps:
            steps += 1
            node = self.nodes[cur]
            nt = node["node_type"]
            if nt == "code":
                self._code(node)
                cur = self._next(node)
            elif nt == "api_request":
                self._api(node)
                cur = self._next(node)
            elif nt == "conversation":
                oe = node.get("on_enter", {})
                if oe.get("type") == "tts":
                    self.spoken.append(self._sub(oe.get("text", ""), self.vars))
                if node.get("passthrough"):
                    cur = self._next(node)
                    continue
                if not turns and node["name"] == "initial_listen":
                    # Preserve the old setup shorthand: no explicit opening
                    # answer means the caller wants the statement flow.
                    t = {"customer_intent": "answer"}
                elif not turns and node["name"] == "speak_q":
                    # The live workflow asks the question and waits for a
                    # caller turn in this node. For a local checkpoint that
                    # only wants to inspect the newly rendered question,
                    # record the prompt and leave the engine waiting.
                    self.spoken.append(str(self.vars.get("question_tts", "")))
                    return
                elif not turns:
                    return  # waiting for the caller
                else:
                    t = turns.pop(0)
                if oe.get("type") == "llm":
                    self.spoken.append("[llm:" + node["name"] + "] " + self._sub(node.get("task_message", "")[:60], self.vars))
                for k, v in t.items():
                    self.vars[k] = v
                cur = self._next(node)
            elif nt == "end_call":
                props = node["terminal"]["function"]["parameters"]["properties"]
                self.exit_text = self._sub(props.get("spoken_message", {}).get("default", ""), self.vars)
                self.spoken.append(self.exit_text)
                self.ended = True
            else:
                raise AssertionError("unknown node type " + nt)


def answers_from_test_data(test_data: Dict[str, Any], services_ir: Dict[str, Any], roles: Dict[str, str], node_to_role: Dict[str, str], overrides=None):
    """Answer api nodes from the case's test data, like the mock does (generic key walk)."""
    customers = test_data["customers"]
    fallback_status = {}
    for n in services_ir["names"]:
        e404 = next((e for e in services_ir["services"][n]["errors"] if e["http"] == 404), None)
        fallback_status[n] = 404 if e404 else 400

    def walk(node, values):
        if isinstance(node, dict) and "http" in node:
            return node
        if not isinstance(node, dict):
            return None
        for v in values:
            for k in node.keys():
                if str(k).strip().lstrip("0") == str(v).strip().lstrip("0") or str(k) == str(v):
                    r = walk(node[k], values)
                    if r is not None:
                        return r
        return None

    def fn(node_name, body):
        role = node_to_role[node_name]
        svc = roles[role]
        if overrides and (node_name in overrides or svc in overrides):
            return overrides.get(node_name) or overrides.get(svc)
        data = body.get("data", body)
        values = [str(v) for v in data.values()]
        for c in customers:
            entry = (c.get("services") or {}).get(svc)
            if entry is None:
                continue
            if str(c["customerNo"]) in values:
                leaf = walk(entry, values)
                if leaf:
                    return leaf["http"], leaf["response"]
                continue
            if not any(str(c2["customerNo"]) in values for c2 in customers):
                leaf = walk(entry, values)
                if leaf:
                    return leaf["http"], leaf["response"]
        return fallback_status[svc], {"result": {"code": "NOT_FOUND"}}
    return fn


NODE_ROLE = {"fetch_list": "list_accounts", "fetch_balance": "balance", "fetch_contact": "contact_get", "send_pdf": "document_send",
             "fetch_cycle": "cycle_get", "fetch_preview": "cycle_preview", "do_change": "cycle_change"}


def main() -> int:
    wf = json.load(open(sys.argv[1], encoding="utf-8"))
    pol = json.load(open(sys.argv[2], encoding="utf-8"))
    sys.path.insert(0, HERE)
    import runtime_case as rt  # type: ignore
    rt.POL = pol
    services_ir = json.load(open(os.path.join(os.path.dirname(sys.argv[1]), os.path.basename(sys.argv[1]).replace(".workflow.json", ".services.json")), encoding="utf-8"))
    td_path = os.environ.get("CASE_TEST_DATA")
    test_data = json.load(open(td_path, encoding="utf-8")) if td_path else None
    roles = pol["service_roles"]
    ph = pol["phrases"]
    cust_var = (pol.get("call_variables") or {}).get("customer_id", "customerNumber")
    results: List[Tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + detail))

    def engine(customer, overrides=None, now="2026-10-12T10:30:00+03:00"):
        fn = answers_from_test_data(test_data, services_ir, roles, NODE_ROLE, overrides)
        e = Engine(wf, fn)
        e.vars[cust_var] = customer
        e.vars["now"] = now
        return e

    def customers_where(pred):
        return [c for c in test_data["customers"] if pred(c)]

    def cards_of(c):
        entry = c["services"].get(roles["list_accounts"], {})
        if entry.get("http") != 200:
            return []
        rf = pol["response_fields"]["list_accounts"]
        return entry["response"]["data"].get(rf["items_path"], [])

    # ---------- runtime unit checks (locale + rendering) ----------
    dom = next(c for c in pol["currencies"] if c.get("is_domestic"))
    sp = rt.amount_spoken(5000.13, dom)
    check("amount in words with minor unit", dom["spoken"] in sp and dom["minor_unit"] in sp and not re.search(r"\d", sp), sp)
    sp0 = rt.amount_spoken(2500.0, dom)
    check("zero minor unit dropped", dom["minor_unit"] not in sp0 and not re.search(r"\d", sp0), sp0)
    check("digits spoken one by one", len(rt.digits_spoken("4253").split()) == 4, rt.digits_spoken("4253"))
    check("date spoken day month year", re.fullmatch(r"\d{1,2} \S+ \d{4}", rt.date_spoken("05.11.2026")) is not None, rt.date_spoken("05.11.2026"))
    big = rt.amount_spoken(128450.75, dom)
    check("large amount in words", not re.search(r"\d", big), big)

    if not test_data:
        print("no CASE_TEST_DATA; runtime checks only")
        return 0 if all(r[1] for r in results) else 1

    # ---------- single card ----------
    single = customers_where(lambda c: len(cards_of(c)) == 1 and c["services"].get(roles["balance"]))
    c1 = single[0]
    card = cards_of(c1)[0]
    e = engine(c1["customerNo"], )
    e.run("setup", [])
    spoken = " ".join(e.spoken)
    check("single card: no which-card question", ph["ask_which_card"].split("{")[0].strip() not in spoken, spoken[:200])
    check("single card: debt read with last4 spoken and no full number", rt.digits_spoken(card["cardNumberLast4"]) in spoken and card["cardNumber"][:6] not in spoken, spoken[:300])
    _nodates = re.sub(r"\d{1,2} \S+ \d{4}", "", e.vars.get("debt_tts", ""))
    check("single card: no raw digits in debt sentence (dates excepted)", not re.search(r"\d", _nodates), e.vars.get("debt_tts", "")[:200])
    check("single card: due date last in read-out", e.vars.get("debt_tts", "").strip().endswith(ph["due_date"].split("{due_date}")[-1].strip()) or ph["due_today"] in e.vars.get("debt_tts", ""), e.vars.get("debt_tts", "")[-80:])
    check("single card: offer asked after contact fetch", any(n == "fetch_contact" for n, _ in e.calls) and e.vars.get("stage") == "offer", str(e.vars.get("stage")))
    check("balance called with the list's card number verbatim", any(n == "fetch_balance" and card["cardNumber"] in json.dumps(b) for n, b in e.calls), str(e.calls[:2]))
    check("no second-factor / customer number question in prompts", "customer number" not in wf["system_prompt"].lower() or "never ask" in wf["system_prompt"].lower())
    # decline follow-ups -> menu
    e.run("listen", [{"customer_intent": "no"}])
    check("offer declined -> menu exit", e.ended and e.exit_text == ph["to_menu"], e.exit_text)

    # ---------- multi-card by name ----------
    multi = customers_where(lambda c: len(cards_of(c)) >= 2)
    cm = next(c for c in multi if len(set(x["productName"] for x in cards_of(c))) == len(cards_of(c)) and all(x["productName"] for x in cards_of(c)))
    cards = cards_of(cm)
    e = engine(cm["customerNo"])
    e.run("setup", [])
    q = e.vars.get("question_tts", "")
    names = [x["productName"] for x in cards]
    check("multi: which-card question with two own names", sum(1 for n in names if n in q) >= 2 and e.vars.get("stage") == "choose_card", q)
    target = cards[-1]
    e.run("listen", [{"customer_intent": "answer", "card_name": target["productName"]}])
    check("multi: name selects the card", e.vars.get("sel_number") == target["cardNumber"], str(e.vars.get("sel_number")))
    check("multi: another-card question before offer", e.vars.get("stage") == "another" and e.vars.get("question_tts") == ph["another_card"], e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "no"}])
    check("multi: no more cards -> offer (or menu when the last card had no statement debt)", e.vars.get("stage") == "offer" or (e.ended and e.exit_text == ph["to_menu"]), str(e.vars.get("stage")))

    # ---------- duplicate names + duplicate last four ----------
    dup = None
    for c in multi:
        cs = cards_of(c)
        byname = {}
        for x in cs:
            byname.setdefault(x["productName"], []).append(x)
        for nm, lst in byname.items():
            if len(lst) >= 2 and len(set(x["cardNumberLast4"] for x in lst)) < len(lst):
                dup = (c, nm, lst)
                break
        if dup:
            break
    if dup:
        c, nm, lst = dup
        same4 = [x for x in lst if sum(1 for y in lst if y["cardNumberLast4"] == x["cardNumberLast4"]) >= 2]
        e = engine(c["customerNo"])
        e.run("setup", [])
        e.run("listen", [{"customer_intent": "answer", "card_name": nm}])
        check("dup: asks last four first", e.vars.get("disamb_level") == "4" and ph["ask_last4"].split("{")[0].strip()[:5] in e.vars.get("question_tts", "") or nm in e.vars.get("question_tts", ""), e.vars.get("question_tts", ""))
        e.run("listen", [{"customer_intent": "answer", "digits": same4[0]["cardNumberLast4"]}])
        check("dup: same last four -> asks last six", e.vars.get("disamb_level") == "6", e.vars.get("question_tts", ""))
        check("dup: last-six question does not echo more than last four digits", not re.search(r"\d{5,}", e.vars.get("question_tts", "")), e.vars.get("question_tts", ""))
        six = re.sub(r"\D", "", same4[0]["cardNumber"])[:6]
        e.run("listen", [{"customer_intent": "answer", "digits": six}])
        check("dup: last six selects the card", e.vars.get("sel_number") == same4[0]["cardNumber"], str(e.vars.get("sel_number")))
        # full-number step when last six also equal (synthetic)
        rt.POL = pol
        v = {"cards_json": json.dumps([{"number": "540000******1111", "last4": "1111", "name": "X"}, {"number": "540000******1111", "last4": "1111", "name": "X"}]), "cards_read_json": "[]", "disamb_level": "6", "cand_json": json.dumps([{"number": "540000******1111", "last4": "1111", "name": "X"}, {"number": "540000******1111", "last4": "1111", "name": "X"}]), "digits": "540000", "card_name": ""}
        r = rt.resolve_card_answer(v)
        check("dup: same last six -> asks full number", v.get("disamb_level") == "10" and r == "ask_again", str(v.get("question_tts")))
    else:
        check("dup: test data has duplicate last-four cards", False, "none found")

    # ---------- unmasked card number from a service: still only last four spoken ----------
    full_list = {"http": 200, "response": {"data": {"cards": [{"cardNumber": "5400001234561234", "cardNumberLast4": "1234", "productName": "Test Kart", "status": "A", "isMainCard": True}], "validCardsCount": 1, "exitState": "1_CARD"}, "result": {"returnCode": "1"}}}
    bal = next(iter(c1["services"][roles["balance"]].values()))
    e = engine(c1["customerNo"], overrides={"fetch_list": (200, full_list["response"]), "fetch_balance": (bal["http"], bal["response"])})
    e.run("setup", [])
    check("full card number from service: never spoken", "5400001234561234" not in " ".join(e.spoken) and rt.digits_spoken("1234") in " ".join(e.spoken), " ".join(e.spoken)[:200])
    check("full card number sent back to balance verbatim", any(n == "fetch_balance" and "5400001234561234" in json.dumps(b) for n, b in e.calls))

    # ---------- no card / service error ----------
    nocard = customers_where(lambda c: c["services"].get(roles["list_accounts"], {}).get("http") == 404)
    if nocard:
        e = engine(nocard[0]["customerNo"])
        e.run("setup", [])
        check("list 404 -> no eligible card sentence, then menu (no transfer)", e.ended and e.exit_text == ph["no_eligible_card"], e.exit_text)
    err = customers_where(lambda c: c["services"].get(roles["list_accounts"], {}).get("http") == 500)
    if err:
        e = engine(err[0]["customerNo"])
        e.run("setup", [])
        check("list 500 -> service error transfer", e.ended and e.exit_text == ph["service_error_transfer"], e.exit_text)
    e = engine(c1["customerNo"], overrides={"fetch_balance": (500, {"result": {"code": "FBE00099"}})})
    e.run("setup", [])
    check("balance 500 -> service error transfer", e.ended and e.exit_text == ph["service_error_transfer"], e.exit_text)

    # ---------- debt variants ----------
    def debt_for(c, cardno=None):
        e = engine(c["customerNo"])
        turns = []
        cs = cards_of(c)
        if len(cs) > 1:
            turns = [{"customer_intent": "answer", "card_name": next(x["productName"] for x in cs if x["cardNumber"] == cardno) if cardno else cs[0]["productName"], "digits": ""}]
        e.run("setup", turns)
        if len(cs) > 1 and e.vars.get("stage") == "choose_card":
            e.run("listen", [{"customer_intent": "answer", "digits": re.sub(r"\D", "", cardno or cs[0]["cardNumber"])[:6]}])
        return e

    rfb = pol["response_fields"]["balance"]
    blocks = rfb["currency_blocks"]
    cur_by_block = {c["block"]: c for c in pol["currencies"]}

    def balance_of(c, cardno):
        return c["services"][roles["balance"]][cardno]["response"]["data"]

    # zero / negative total
    neg = None
    for c in test_data["customers"]:
        for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
            if leaf["http"] == 200:
                d = leaf["response"]["data"]
                for b in blocks:
                    if isinstance(d.get(b), dict) and float(d[b].get(rfb["total_debt"], 0)) < 0:
                        neg = (c, cardno)
    if neg:
        e = debt_for(*neg)
        t = e.vars.get("debt_tts", "")
        check("negative balance -> no-debt wording, total read as the service returned it (signed), not rounded to zero", e.vars.get("had_statement_debt") == "no" and ("eksi" in t or "minus" in t) and (" sıfır " not in t), t[:220])
    # missing block
    miss = None
    for c in test_data["customers"]:
        for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
            if leaf["http"] == 200 and any(b not in leaf["response"]["data"] for b in blocks):
                miss = (c, cardno)
    if miss:
        e = debt_for(*miss)
        d = balance_of(*miss)
        missing = [cur_by_block[b]["spoken"] for b in blocks if b not in d]
        t = e.vars.get("debt_tts", "")
        want_in = bool(pol["readout"].get("missing_block_in_no_debt_sentence"))
        check("missing currency block not invented", all((m in t) == want_in for m in missing) or (not want_in and all(m not in t for m in missing)), t[:250])
    # multi-currency debt
    multi_cur = None
    for c in test_data["customers"]:
        for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
            if leaf["http"] == 200:
                d = leaf["response"]["data"]
                if sum(1 for b in blocks if isinstance(d.get(b), dict) and float(d[b].get(rfb["remaining_debt"], 0)) > 0) >= 3:
                    multi_cur = (c, cardno)
    if multi_cur:
        e = debt_for(*multi_cur)
        t = e.vars.get("debt_tts", "")
        d = balance_of(*multi_cur)
        order_ok = True
        pos = -1
        for b in blocks:
            if isinstance(d.get(b), dict) and float(d[b].get(rfb["remaining_debt"], 0)) > 0:
                p = t.find(cur_by_block[b]["spoken"] + " borcunuz") if pol.get("language") == "tr" else t.find(cur_by_block[b]["spoken"])
                if p < pos:
                    order_ok = False
                pos = max(pos, p)
        check("multi-currency: currencies in policy order, full names, no codes", order_ok and not re.search(r"\b(USD|EUR|GBP|TL)\b", t), t[:300])
    # TL branches: unpaid / min paid / partial
    def tl_case(pred):
        for c in test_data["customers"]:
            for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
                if leaf["http"] == 200:
                    d = leaf["response"]["data"].get(dom["block"])
                    if isinstance(d, dict) and pred(d):
                        return c, cardno, d
        return None
    f_sd, f_mn, f_rd, f_rm = rfb["statement_debt"], rfb["min_payment"], rfb["remaining_debt"], rfb["remaining_min_payment"]
    unpaid = tl_case(lambda d: d[f_rd] > 0 and d[f_rm] >= d[f_mn] > 0)
    minpaid = tl_case(lambda d: d[f_rd] > 0 and d[f_rm] <= 0)
    partial = tl_case(lambda d: d[f_rd] > 0 and 0 < d[f_rm] < d[f_mn])
    for label, case_, key in (("unpaid", unpaid, "tl_unpaid"), ("minimum paid", minpaid, "tl_min_paid"), ("partial", partial, "tl_partial")):
        if case_:
            e = debt_for(case_[0], case_[1])
            t = e.vars.get("debt_tts", "")
            marker = ph[key].split("{")[0].strip()
            tail = ph[key].split("}")[-1].strip()[-25:]
            check(f"TL branch {label} uses {key}", (tail and tail in t) or marker in t, t[:250])
    # no statement debt but total debt
    nostmt = None
    for c in test_data["customers"]:
        for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
            if leaf["http"] == 200:
                d = leaf["response"]["data"]
                if all(not (isinstance(d.get(b), dict) and float(d[b].get(f_rd, 0)) > 0) for b in blocks) and float(d[dom["block"]].get(rfb["total_debt"], 0)) > 0 and d.get(rfb["statement_date"]):
                    nostmt = (c, cardno)
    if nostmt:
        e = debt_for(*nostmt)
        t = e.vars.get("debt_tts", "")
        check("no statement debt -> total incl. future periods sentence", ph["no_statement_debt"].split("{")[0].strip()[:4] in t and e.vars.get("had_statement_debt") == "no", t[:200])
        check("no statement debt -> no follow-up offer when policy says so", e.ended == (not pol["readout"].get("offer_after_no_statement_debt")) or len(cards_of(nostmt[0])) > 1, str(e.ended))
    # empty statement date (no statement yet) never invents a date
    empty = None
    for c in test_data["customers"]:
        for cardno, leaf in (c["services"].get(roles["balance"]) or {}).items():
            if leaf["http"] == 200 and not leaf["response"]["data"].get(rfb["statement_date"]):
                empty = (c, cardno)
    if empty:
        e = debt_for(*empty)
        t = e.vars.get("debt_tts", "")
        check("no statement yet: no invented date in sentence", not re.search(r"\d{4}", t) and " 20" not in t, t[:200])

    # ---------- email ----------
    def to_offer(c, cardno=None):
        e = debt_for(c, cardno)
        while not e.ended and e.vars.get("stage") == "another":
            e.run("listen", [{"customer_intent": "no"}])
        return e
    emailc = next(c for c in single if (c["services"].get(roles["contact_get"], {}).get("response", {}).get("data", {}) or {}).get(pol["response_fields"]["contact_get"]["email"]) and c["services"].get(roles["document_send"], {}).get("http") == 200 and c["services"][roles["balance"]][cards_of(c)[0]["cardNumber"]]["response"]["data"][dom["block"]][f_rd] > 0)
    e = to_offer(emailc)
    check("offer includes e-mail when registered", e.vars.get("question_tts") == ph["offer_both"], e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "wants_email"}])
    email = emailc["services"][roles["contact_get"]]["response"]["data"][pol["response_fields"]["contact_get"]["email"]]
    check("email: confirmation question reads registered address", email in e.vars.get("question_tts", "") and e.vars.get("stage") == "email_confirm" and not any(n == "send_pdf" for n, _ in e.calls), e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "unclear"}])
    check("email: unclear answer -> no send, asks again", not any(n == "send_pdf" for n, _ in e.calls) and not e.ended, str(e.vars.get("question_tts")))
    e.run("listen", [{"customer_intent": "yes"}])
    sent = [b for n, b in e.calls if n == "send_pdf"]
    check("email: sent only after yes, to registered address with balance statement date", len(sent) == 1 and email in json.dumps(sent[0]) and e.vars.get("stmt_date_raw") in json.dumps(sent[0]), json.dumps(sent[:1], ensure_ascii=False)[:300])
    check("email: success sentence then menu", e.ended and e.exit_text.startswith(ph["email_sent"]) and e.exit_text.endswith(ph["to_menu"]), e.exit_text)
    # no email registered
    noemail = next((c for c in single if c["services"].get(roles["contact_get"], {}).get("http") == 200 and not (c["services"].get(roles["contact_get"], {}).get("response", {}).get("data", {}) or {}).get(pol["response_fields"]["contact_get"]["email"]) and c["services"][roles["balance"]][cards_of(c)[0]["cardNumber"]]["response"]["data"][dom["block"]][f_rd] > 0), None)
    if noemail:
        e = to_offer(noemail)
        check("no e-mail: offer only week change", e.vars.get("question_tts") == ph["offer_change_only"], e.vars.get("question_tts", ""))
        e.run("listen", [{"customer_intent": "wants_email"}])
        check("no e-mail: asks anyway -> no_email sentence, never asks for an address, no send", ph["no_email"] in " ".join(e.spoken) and not any(n == "send_pdf" for n, _ in e.calls), noemail["customerNo"] + " | " + " || ".join(e.spoken[-3:]))
    # pdf 404
    pdf404 = next((c for c in test_data["customers"] if c["services"].get(roles["document_send"], {}).get("http") == 404), None)
    if pdf404:
        cs = cards_of(pdf404)
        e = to_offer(pdf404, cs[0]["cardNumber"])
        e.run("listen", [{"customer_intent": "wants_email"}])
        e.run("listen", [{"customer_intent": "yes"}])
        check("pdf 404 -> honest failure, never says sent", e.ended and ph["email_sent"] not in e.exit_text, e.exit_text)
    # contact service error
    cerr = next((c for c in test_data["customers"] if c["services"].get(roles["contact_get"], {}).get("http") == 500), None)
    if cerr:
        e = to_offer(cerr, cards_of(cerr)[0]["cardNumber"])
        check("contact 500 -> e-mail not offered", e.vars.get("question_tts") in (ph["offer_change_only"],) or e.ended, e.vars.get("question_tts", ""))

    # ---------- statement week change ----------
    cg = pol["response_fields"]["cycle_get"]
    shared = next(c for c in test_data["customers"] if any(l["http"] == 200 and l["response"]["data"].get(cg["relation_type"]) for l in (c["services"].get(roles["cycle_get"]) or {}).values()))
    cardno = next(k for k, l in shared["services"][roles["cycle_get"]].items() if l["http"] == 200 and l["response"]["data"].get(cg["relation_type"]))
    e = to_offer(shared, cardno)
    e.run("listen", [{"customer_intent": "wants_change"}])
    cyc = shared["services"][roles["cycle_get"]][cardno]["response"]["data"]
    check("week: current week read as ordinal word, asks whether to change", rt.ordinal_word(cyc[cg["week"]]) in e.vars.get("question_tts", "") and e.vars.get("stage") == "cycle_ask", e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "yes"}])
    opts = e.vars.get("question_tts", "")
    check("week: options exclude current week", rt.ordinal_word(cyc[cg["week"]]) not in opts and e.vars.get("stage") == "cycle_week", opts)
    e.run("listen", [{"customer_intent": "answer", "week_choice": str(cyc[cg["week"]])}])
    check("week: current week chosen -> no preview call", not any(n == "fetch_preview" for n, _ in e.calls), str(e.calls[-1]))
    newweek = next(w for w in range(1, 5) if w != int(cyc[cg["week"]]))
    e.run("listen", [{"customer_intent": "answer", "week_choice": str(newweek)}])
    prev_calls = [b for n, b in e.calls if n == "fetch_preview"]
    check("week: preview called with chosen week and current day", len(prev_calls) == 1 and str(newweek) in json.dumps(prev_calls[0]) and str(cyc[cg["day"]]) in json.dumps(prev_calls[0]), json.dumps(prev_calls[:1]))
    q = e.vars.get("question_tts", "")
    related = cyc[cg["related_name"]]
    disc = ph["cycle_shared_impact"].replace("{related_card_name}", related)
    check("week: shared-card disclosure before confirmation question", disc in q and q.index(disc) < q.index(ph["cycle_confirm"].split("{")[0].strip()[:8]) and e.vars.get("stage") == "cycle_confirm", q)
    check("week: no change call before confirmation", not any(n == "do_change" for n, _ in e.calls))
    e.run("listen", [{"customer_intent": "unclear"}])
    check("week: hedged answer -> asks for clear yes, no change", not any(n == "do_change" for n, _ in e.calls) and not e.ended, e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "yes"}])
    chg = [b for n, b in e.calls if n == "do_change"]
    prev = shared["services"][roles["cycle_preview"]][cardno][str(newweek)]["response"]["data"]
    pf = pol["response_fields"]["cycle_preview"]
    check("week: change called once after yes with same week/day", len(chg) == 1 and str(newweek) in json.dumps(chg[0]) and str(cyc[cg["day"]]) in json.dumps(chg[0]), json.dumps(chg))
    check("week: success sentence uses PREVIEW dates then menu", rt.date_spoken(prev[pf["statement_date"]]) in e.exit_text and rt.date_spoken(prev[pf["due_date"]]) in e.exit_text and e.exit_text.endswith(ph["to_menu"]), e.exit_text)
    # week preview failure
    werr = next((c for c in test_data["customers"] if any(any(x["http"] == 500 for x in l.values()) for l in (c["services"].get(roles["cycle_preview"]) or {}).values())), None)
    if werr:
        cn = next(k for k, l in werr["services"][roles["cycle_preview"]].items() if any(x["http"] == 500 for x in l.values()))
        e = to_offer(werr, cn)
        e.run("listen", [{"customer_intent": "wants_change"}])
        e.run("listen", [{"customer_intent": "yes"}])
        cw = int(werr["services"][roles["cycle_get"]][cn]["response"]["data"][cg["week"]])
        e.run("listen", [{"customer_intent": "answer", "week_choice": str(next(w for w in range(1, 5) if w != cw))}])
        check("week: preview 500 -> cycle failure transfer sentence", e.ended and e.exit_text == ph["cycle_fail_transfer"], e.exit_text)
    # week get failure
    gerr = next((c for c in test_data["customers"] if any(l["http"] == 500 for l in (c["services"].get(roles["cycle_get"]) or {}).values())), None)
    if gerr:
        e = to_offer(gerr, cards_of(gerr)[0]["cardNumber"])
        e.run("listen", [{"customer_intent": "wants_change"}])
        check("week: get 500 -> cycle failure transfer sentence", e.ended and e.exit_text == ph["cycle_fail_transfer"], e.exit_text)

    # ---------- silence / attempts / agent / complaint / out of scope ----------
    e = engine(cm["customerNo"])
    e.run("setup", [])
    full_q = e.vars.get("question_tts")
    sil = int(pol["limits"]["silence_repeats_before_end"])
    for i in range(sil):
        e.run("listen", [{"customer_intent": "silence"}])
        check(f"silence {i + 1}: short re-ask, call continues", not e.ended and e.vars.get("question_tts") == e.vars.get("question_short") and len(e.vars.get("question_tts")) <= len(full_q), e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "silence"}])
    check("silence limit -> ends call with silence sentence", e.ended and e.exit_text == ph["silence_exit"], e.exit_text)
    e = engine(cm["customerNo"])
    e.run("setup", [])
    att = int(pol["limits"]["attempts_per_question"])
    for i in range(att - 1):
        e.run("listen", [{"customer_intent": "answer", "card_name": "yok böyle kart"}])
        check(f"failed attempt {i + 1}: lists cards again", not e.ended and e.vars.get("stage") == "choose_card", e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "answer", "card_name": "yok böyle kart"}])
    check("failed attempts limit -> failure exit sentence", e.ended and e.exit_text == ph["failed_attempts_exit"], e.exit_text)
    e = engine(cm["customerNo"])
    e.run("setup", [{"customer_intent": "all_accounts"}])
    check("'all cards' counts as a failed attempt and re-asks", e.vars.get("attempts") == "1" and not e.ended, str(e.vars.get("attempts")))
    for intent, key in (("wants_agent", "agent_transfer"), ("wants_pay", "payment_transfer"), ("dispute", "dispute_transfer"), ("other_language", "other_language_exit")):
        e = engine(cm["customerNo"])
        e.run("setup", [{"customer_intent": intent}])
        check(f"{intent} -> immediate exit with {key}", e.ended and e.exit_text == ph[key], e.exit_text)
    e = engine(cm["customerNo"])
    e.run("setup", [])
    lim_c = int(pol["limits"]["complaint_replies_before_transfer"])
    for i in range(lim_c):
        e.run("listen", [{"customer_intent": "complaint"}])
        check(f"complaint {i + 1}: calming reply, not transferred", not e.ended and e.vars.get("route") == "calm", str(e.vars.get("route")))
    e.run("calm", [{"customer_intent": "complaint"}])
    check("complaint limit -> transfer", e.ended and e.exit_text == ph["agent_transfer"], e.exit_text)
    e = engine(cm["customerNo"])
    e.run("setup", [{"customer_intent": "out_of_scope"}])
    check("out of scope -> asks about menu", e.vars.get("question_tts") == ph["out_of_scope_ask"] and not e.ended, e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "no"}])
    check("out of scope: continue -> resumes same question", e.vars.get("stage") == "choose_card" and e.vars.get("question_tts") == full_q, e.vars.get("question_tts", ""))
    e.run("listen", [{"customer_intent": "out_of_scope"}])
    e.run("listen", [{"customer_intent": "yes"}])
    check("out of scope: yes -> menu", e.ended and e.exit_text == ph["to_menu"], e.exit_text)
    e = engine(cm["customerNo"])
    e.run("setup", [{"customer_intent": "repeat"}])
    check("repeat -> same question again", e.vars.get("question_tts") == full_q and not e.ended)

    # ---------- prompt injection in a product name ----------
    inj = None
    for c in test_data["customers"]:
        for x in cards_of(c):
            if len(x["productName"].split()) >= 6:
                inj = (c, x)
    if inj:
        c, x = inj
        e = engine(c["customerNo"])
        e.run("setup", [])
        others = [y for y in cards_of(c) if y is not x]
        e.run("listen", [{"customer_intent": "answer", "card_name": others[0]["productName"]}])
        allsp = " ".join(e.spoken)
        check("injection: product-name text is data; no full numbers spoken", not re.search(r"\d{7,}", allsp) and all(y["cardNumber"][:6] not in allsp for y in cards_of(c)), allsp[:200])
        check("injection: system prompt tells the model service text is data", "DATA" in wf["system_prompt"] or "data" in wf["system_prompt"])

    # ---------- body baking uses variables only ----------
    for n, node in wf["nodes"].items():
        if node["node_type"] == "api_request":
            props = node["pre_actions"][0]["function"]["parameters"]["properties"]["body"]["properties"]
            flat = json.dumps(props)
            check(f"api node {n}: no model-filled field", "{{unset}}" not in flat)
    check("workflow has no tools for the dialogue model", all(not node.get("tools") for node in wf["nodes"].values()))

    # ---------- mock exact responses for all customers (same walker as the mock) ----------
    n_ok = 0
    n_all = 0
    fn = answers_from_test_data(test_data, services_ir, roles, NODE_ROLE)
    for c in test_data["customers"]:
        entry = c["services"].get(roles["list_accounts"])
        if entry:
            n_all += 1
            st, body = fn("fetch_list", {"data": {"customerNo": c["customerNo"]}})
            if st == entry["http"] and body == entry["response"]:
                n_ok += 1
    check("test-data lookup reproduces every customer's list answer", n_ok == n_all, f"{n_ok}/{n_all}")

    passed = sum(1 for r in results if r[1])
    print(f"offline case tests: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
