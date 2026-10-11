# Runtime for case-driven read-out flows. This file is embedded verbatim into every code node of the
# compiled workflow (no double curly braces allowed, only json/re/datetime imports, never raise). The policy dict POL is
# injected by the compiler before this text; everything bank-specific is read from POL at run time.
import json
import re
import datetime

try:
    POL
except NameError:  # local tests inject POL after import
    POL = {}

# ---------- locale resources (language libraries, not case data) ----------
_TR_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TR_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_TR_SCALES = [(1000000000, "milyar"), (1000000, "milyon"), (1000, "bin")]
_TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
_TR_ORD = {1: "birinci", 2: "ikinci", 3: "üçüncü", 4: "dördüncü", 5: "beşinci", 6: "altıncı"}
_TR_DIGITS = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]

_EN_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_EN_ORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
_EN_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _lang():
    return str(POL.get("language") or "en").lower()[:2]


def _tr_below_thousand(n):
    parts = []
    h = n // 100
    if h == 1:
        parts.append("yüz")
    elif h > 1:
        parts.append(_TR_ONES[h] + " yüz")
    t = (n % 100) // 10
    if t:
        parts.append(_TR_TENS[t])
    o = n % 10
    if o:
        parts.append(_TR_ONES[o])
    return " ".join(parts)


def _tr_int_words(n):
    if n == 0:
        return "sıfır"
    parts = []
    for val, name in _TR_SCALES:
        q = n // val
        if q:
            if val == 1000 and q == 1:
                parts.append("bin")
            else:
                parts.append(_tr_below_thousand(q) + " " + name)
            n = n % val
    if n:
        parts.append(_tr_below_thousand(n))
    return " ".join(p for p in parts if p)


def _en_below_thousand(n):
    parts = []
    if n >= 100:
        parts.append(_EN_ONES[n // 100] + " hundred")
        n = n % 100
    if n >= 20:
        parts.append(_EN_TENS[n // 10] + ("-" + _EN_ONES[n % 10] if n % 10 else ""))
    elif n:
        parts.append(_EN_ONES[n])
    return " ".join(parts)


def _en_int_words(n):
    if n == 0:
        return "zero"
    parts = []
    for val, name in [(1000000000, "billion"), (1000000, "million"), (1000, "thousand")]:
        q = n // val
        if q:
            parts.append(_en_below_thousand(q) + " " + name)
            n = n % val
    if n:
        parts.append(_en_below_thousand(n))
    return " ".join(parts)


def int_words(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    neg = n < 0
    n = abs(n)
    w = _tr_int_words(n) if _lang() == "tr" else _en_int_words(n)
    if neg:
        w = ("eksi " if _lang() == "tr" else "minus ") + w
    return w


def digits_spoken(s):
    s = re.sub(r"\D", "", str(s or ""))
    names = _TR_DIGITS if _lang() == "tr" else _EN_DIGITS
    return " ".join(names[int(c)] for c in s)


def ordinal_word(n):
    try:
        n = int(n)
    except Exception:
        return str(n)
    table = _TR_ORD if _lang() == "tr" else _EN_ORD
    return table.get(n, str(n) + ".")


def _parse_date(s):
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass
    return None


def date_spoken(s):
    d = _parse_date(s)
    if not d:
        return str(s or "")
    months = _TR_MONTHS if _lang() == "tr" else _EN_MONTHS
    return "%d %s %d" % (d.day, months[d.month - 1], d.year)


def _now_date(vars):
    raw = str(vars.get(str((POL.get("call_variables") or {}).get("now") or "now")) or vars.get("now") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def amount_spoken(x, cur):
    """x: number; cur: currency dict from POL['currencies']."""
    try:
        v = float(x)
    except Exception:
        return str(x)
    neg = v < 0
    v = abs(v)
    major = int(v + 1e-9)
    minor = int(round((v - major) * 100))
    if minor >= 100:
        major += 1
        minor -= 100
    spoken = str(cur.get("spoken") or cur.get("code") or "")
    minor_name = str(cur.get("minor_unit") or "")
    out = int_words(major) + " " + spoken
    drop0 = (POL.get("readout") or {}).get("drop_zero_minor", True)
    if minor or not drop0:
        out += " " + int_words(minor) + (" " + minor_name if minor_name else "")
    if neg:
        out = ("eksi " if _lang() == "tr" else "minus ") + out
    return out.strip()


def fill(key, **kw):
    t = (POL.get("phrases") or {}).get(key) or ""
    try:
        for k, v in kw.items():
            t = t.replace("{" + k + "}", str(v))
    except Exception:
        pass
    return t.strip()


def join_names(items):
    items = [str(i) for i in items if str(i)]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    conj = " ve " if _lang() == "tr" else " and "
    return ", ".join(items[:-1]) + conj + items[-1]


# ---------- variable helpers ----------
def _unflatten(vars, pref):
    """Rebuild a nested value from flattened keys  pref.a.0.b  (lists from digit keys, "null"/"true"/"false" strings restored)."""
    out = {}
    found = False
    for k in list(vars.keys()):
        if isinstance(k, str) and k.startswith(pref):
            found = True
            rest = k[len(pref):]
            cur = out
            bits = rest.split(".")
            for b in bits[:-1]:
                if not isinstance(cur.get(b), dict):
                    cur[b] = {}
                cur = cur[b]
            v = vars.get(k)
            if isinstance(v, str):
                if v == "null":
                    v = None
                elif v == "true":
                    v = True
                elif v == "false":
                    v = False
            cur[bits[-1]] = v
    if not found:
        return None

    def fix(o):
        if isinstance(o, dict):
            if o and all(str(k).isdigit() for k in o.keys()):
                return [fix(o[k]) for k in sorted(o.keys(), key=lambda x: int(x))]
            return dict((k, fix(v)) for k, v in o.items())
        return o
    return fix(out)


def obj(vars, name):
    """Return a dict/list variable whether it arrived as object, JSON string or flattened keys."""
    v = vars.get(name)
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v.strip()[:1] in ("{", "["):
        try:
            return json.loads(v)
        except Exception:
            pass
    flat = _unflatten(vars, name + ".")
    if flat is not None:
        return flat
    return v if v is not None else {}


def resp(vars, node):
    """(status_code, data) for an api_request node named `node`."""
    r = obj(vars, node + "_response")
    status = None
    data = None
    if isinstance(r, dict):
        status = r.get("status_code")
        data = r.get("data")
    if status is None:
        status = vars.get(node + "_response.status_code")
    try:
        status = int(str(status)) if status is not None and str(status).strip() else None
    except Exception:
        status = None
    if isinstance(data, str) and data.strip()[:1] in ("{", "["):
        try:
            data = json.loads(data)
        except Exception:
            pass
    # the bank envelope: body = data + result -> engine data is the body
    if isinstance(data, dict) and isinstance(data.get("data"), (dict, list)):
        data = data.get("data")
    elif isinstance(data, dict) and "result" in data and "data" not in data:
        data = None
    if status is None:
        fc = vars.get(node + "_failure_count")
        sc = vars.get(node + "_success_count")
        try:
            if fc is not None and int(str(fc)) > 0:
                status = 599
            elif sc is not None and int(str(sc)) > 0:
                status = 200
        except Exception:
            pass
    if status is None and isinstance(data, (dict, list)) and data:
        status = 200
    return status, data


def mapped(vars, name, fallback_node=None, path=None):
    v = obj(vars, name)
    if v not in ({}, None, ""):
        return v
    if fallback_node and path:
        st, data = resp(vars, fallback_node)
        cur = data
        for p in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                cur = None
                break
        return cur
    return None


def num(x, default=None):
    try:
        if x is None or str(x).strip() == "":
            return default
        return float(str(x).replace(",", "."))
    except Exception:
        return default


def sget(vars, k, default=""):
    v = vars.get(k)
    return default if v is None else v


def iget(vars, k, default=0):
    try:
        return int(str(vars.get(k)))
    except Exception:
        return default


def set_question(vars, stage, text, short=None):
    vars["stage"] = stage
    vars["question_tts"] = text
    vars["question_short"] = short or text
    vars["last_tts"] = text


def say_exit(vars, text, kind):
    vars["exit_tts"] = text
    vars["exit_kind"] = kind
    vars["route"] = "exit"


def limit(name, default):
    try:
        return int((POL.get("limits") or {}).get(name, default))
    except Exception:
        return default


# ---------- cards ----------
def parse_cards(vars, items):
    rf = (POL.get("response_fields") or {}).get("list_accounts") or {}
    out = []
    if isinstance(items, dict):
        items = items.get(rf.get("items_path") or "cards") or []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        number = str(it.get(rf.get("item_number") or "cardNumber") or "")
        last4 = str(it.get(rf.get("item_last4") or "cardNumberLast4") or "")
        if not last4:
            last4 = re.sub(r"\D", "", number)[-4:]
        name = str(it.get(rf.get("item_name") or "productName") or "").strip()
        main = it.get(rf.get("item_main_flag") or "isMainCard")
        out.append({"number": number, "last4": last4, "name": name, "main": main})
    return out


def card_label(c):
    """How the card is referred to in a question: the product name, else 'ending in <digits>' phrase via debt_intro_no_name is not usable here, so use spoken last4."""
    if c.get("name"):
        return c["name"]
    return digits_spoken(c.get("last4"))


def norm(s):
    s = str(s or "").lower()
    s = s.replace("ı", "i").replace("İ", "i").replace("ş", "s").replace("ğ", "g").replace("ü", "u").replace("ö", "o").replace("ç", "c")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def match_cards_by_name(cards, said):
    n = norm(said)
    if not n:
        return []
    exact = [c for c in cards if norm(c["name"]) == n]
    if exact:
        return exact
    toks = [t for t in n.split() if len(t) > 1]
    # prefer cards whose full name is contained in what was said or vice versa
    contained = [c for c in cards if c["name"] and (norm(c["name"]) in n or n in norm(c["name"]))]
    if contained:
        # when several share the longest match, keep those with the longest name match
        best = max(len(norm(c["name"])) for c in contained)
        return [c for c in contained if len(norm(c["name"])) == best]
    scored = []
    for c in cards:
        cn = set(norm(c["name"]).split())
        if not cn:
            continue
        overlap = len(cn & set(toks))
        if overlap:
            scored.append((overlap / float(len(cn)), c))
    if scored:
        top = max(s for s, _ in scored)
        if top >= 0.5:
            return [c for s, c in scored if s == top]
    return []


def match_cards_by_digits(cards, digits):
    d = re.sub(r"\D", "", str(digits or ""))
    if not d:
        return []
    if len(d) >= 7:
        full = [c for c in cards if re.sub(r"\D", "", c["number"]) == d or (re.sub(r"\D", "", c["number"]).endswith(d[-4:]) and re.sub(r"\D", "", c["number"]).startswith(d[:6]))]
        if full:
            return full
    if len(d) >= 6:
        six = [c for c in cards if re.sub(r"\D", "", c["number"]).startswith(d[:6])]
        if len(d) == 6 and six:
            return six
    if len(d) >= 4:
        return [c for c in cards if c["last4"] == d[-4:]]
    return []


def select_card(vars, c):
    vars["sel_card"] = json.dumps(c, ensure_ascii=False)
    vars["sel_number"] = c["number"]
    vars["sel_last4"] = c["last4"]
    vars["sel_name"] = c["name"]
    vars["sel_last4_spoken"] = digits_spoken(c["last4"])
    vars["cand_json"] = ""
    vars["disamb_level"] = "0"
    vars["route"] = "selected"


def remaining_cards(vars):
    cards = obj(vars, "cards_json") or []
    done = obj(vars, "cards_read_json") or []
    return [c for c in cards if c["number"] not in done]


def ask_card_question(vars, cards, repeat_short=False):
    """Set the 'which card' question with up to two example names from the caller's own cards."""
    names = []
    for c in cards:
        lab = card_label(c)
        if lab and lab not in names:
            names.append(lab)
    ex = names[:2]
    if len(ex) == 2:
        text = fill("ask_which_card", example1=ex[0], example2=ex[1])
    elif len(ex) == 1:
        text = fill("ask_which_card", example1=ex[0], example2=ex[0])
    else:
        text = fill("ask_which_card_short") or fill("ask_which_card", example1="", example2="")
    short = fill("ask_which_card_short") or text.split(".")[0].strip() + ("?" if "?" not in text.split(".")[0] else "")
    set_question(vars, "choose_card", short if repeat_short else text, short)


def ask_disambiguation(vars, cands):
    """cands share a name (level 4), a last4 (level 6) or last6 (level full)."""
    level = iget(vars, "disamb_level", 0)
    vars["cand_json"] = json.dumps(cands, ensure_ascii=False)
    if level < 4:
        vars["disamb_level"] = "4"
        text = fill("ask_last4", card_name=cands[0]["name"] or digits_spoken(cands[0]["last4"]))
    elif level < 6:
        vars["disamb_level"] = "6"
        text = fill("ask_last6", last4=digits_spoken(cands[0]["last4"]))
    else:
        vars["disamb_level"] = "10"
        text = fill("ask_full_number")
    set_question(vars, "choose_card", text, text)


def resolve_card_answer(vars, said_name=None, digits=None):
    """Use the caller's card name / digits against the remaining cards (or current candidates)."""
    cards = remaining_cards(vars)
    cands = obj(vars, "cand_json") or []
    pool = cands if cands else cards
    said_name = str(sget(vars, "card_name") if said_name is None else said_name).strip()
    digits = re.sub(r"\D", "", str(sget(vars, "digits") if digits is None else digits))
    level = iget(vars, "disamb_level", 0)
    hits = []
    if digits and level > 0:
        hits = match_cards_by_digits(pool, digits)
        if not hits:
            hits = match_cards_by_digits(cards, digits)
    if not hits and said_name:
        hits = match_cards_by_name(pool, said_name)
        if not hits and cands:
            hits = match_cards_by_name(cards, said_name)
    if not hits and digits and level == 0:
        hits = match_cards_by_digits(cards, digits)
    if len(hits) == 1:
        select_card(vars, hits[0])
        return "selected"
    if len(hits) > 1:
        # several cards still match -> next disambiguation level
        same_last4 = len(set(h["last4"] for h in hits)) == 1
        same_six = len(set(re.sub(r"\D", "", h["number"])[:6] for h in hits)) == 1
        if level == 0:
            vars["disamb_level"] = "0"
        elif level == 4 and same_last4:
            vars["disamb_level"] = "4"
        elif level >= 4 and same_last4 and same_six:
            vars["disamb_level"] = "6"
        ask_disambiguation(vars, hits)
        return "ask_again"
    return "not_found"


# ---------- balance ----------
def currency_list():
    return [c for c in (POL.get("currencies") or []) if isinstance(c, dict)]


def render_debt(vars, data):
    rf = (POL.get("response_fields") or {}).get("balance") or {}
    ro = POL.get("readout") or {}
    card = obj(vars, "sel_card") or {}
    vars["has_credit_balance"] = "no"
    name = str(card.get("name") or "")
    last4s = digits_spoken(card.get("last4"))
    stmt = str(data.get(rf.get("statement_date") or "statementDate") or "")
    due = str(data.get(rf.get("due_date") or "lastPayDate") or "")
    vars["stmt_date_raw"] = stmt
    vars["due_date_raw"] = due
    f_sd = rf.get("statement_debt") or "statementDebt"
    f_min = rf.get("min_payment") or "minPayment"
    f_rd = rf.get("remaining_debt") or "remainingDebt"
    f_rm = rf.get("remaining_min_payment") or "remainingMinPayment"
    f_td = rf.get("total_debt") or "totalDebt"
    f_auto_amt = rf.get("auto_payment_amount") or "remainAutomaticPaymentAmount"
    auto_flag = str(data.get(rf.get("auto_payment_flag") or "automaticPaymentFlag") or "")
    auto_yes = str(rf.get("auto_payment_flag_yes") or "Y")
    missing_in_no_debt = bool(ro.get("missing_block_in_no_debt_sentence", False))
    sentences = []
    no_debt = []
    any_debt = False
    total_dom = None
    dom_cur = None
    auto_total = 0.0
    for cur in currency_list():
        block = data.get(cur.get("block"))
        if not isinstance(block, dict):
            if missing_in_no_debt:
                no_debt.append(cur.get("spoken"))
            continue
        sd = num(block.get(f_sd), 0.0)
        mn = num(block.get(f_min), 0.0)
        rd = num(block.get(f_rd), 0.0)
        rm = num(block.get(f_rm), 0.0)
        td = num(block.get(f_td), 0.0)
        auto_total += num(block.get(f_auto_amt), 0.0) or 0.0
        if cur.get("is_domestic"):
            dom_cur = cur
            total_dom = td
        has = rd is not None and rd > 0
        if not has:
            no_debt.append(cur.get("spoken"))
            continue
        any_debt = True
        sp = cur.get("spoken")
        if cur.get("is_domestic"):
            if rm is not None and mn is not None and rm >= mn and mn > 0:
                s = fill("tl_unpaid", currency=sp, statement_debt=amount_spoken(sd, cur), min_payment=amount_spoken(mn, cur))
            elif rm is not None and rm <= 0:
                s = fill("tl_min_paid", currency=sp, statement_debt=amount_spoken(sd, cur), remaining_debt=amount_spoken(rd, cur))
            else:
                s = fill("tl_partial", currency=sp, statement_debt=amount_spoken(sd, cur), min_payment=amount_spoken(mn, cur), remaining_debt=amount_spoken(rd, cur), remaining_min=amount_spoken(rm, cur))
        else:
            s = fill("fx_debt", currency=sp, statement_debt=amount_spoken(sd, cur), remaining_debt=amount_spoken(rd, cur), min_payment=amount_spoken(mn, cur))
        sentences.append(s)
    if not any_debt:
        dom = dom_cur or (currency_list()[0] if currency_list() else {"spoken": ""})
        # the total is read exactly as the service returned it (a negative value is spoken signed, never rounded to zero)
        total_txt = amount_spoken(total_dom if total_dom is not None else 0.0, dom)
        if not stmt and fill("no_statement_yet"):
            text = fill("no_statement_yet", last4_spoken=last4s, card_name=name, total_debt=total_txt)
        else:
            text = fill("no_statement_debt", last4_spoken=last4s, card_name=name, statement_date=date_spoken(stmt), total_debt=total_txt, due_date=date_spoken(due))
        if total_dom is not None and total_dom < 0 and fill("credit_balance"):
            vars["has_credit_balance"] = "yes"
            text = text + " " + fill("credit_balance", amount=amount_spoken(abs(total_dom), dom))
        vars["debt_tts"] = text
        vars["had_statement_debt"] = "no"
        return text
    intro = fill("debt_intro", last4_spoken=last4s, card_name=name, statement_date=date_spoken(stmt)) if name else fill("debt_intro_no_name", last4_spoken=last4s, statement_date=date_spoken(stmt))
    parts = [intro + " " + sentences[0]] + sentences[1:]
    if no_debt:
        parts.append(fill("no_debt_currencies", currency_list=join_names(no_debt)))
    if auto_flag == auto_yes and auto_total > 0 and fill("auto_payment"):
        parts.append(fill("auto_payment", amount=amount_spoken(auto_total, dom_cur or currency_list()[0])))
    today = _now_date(vars)
    dd = _parse_date(due)
    if dd and today and dd == today and fill("due_today"):
        parts.append(fill("due_today"))
    elif due:
        parts.append(fill("due_date", due_date=date_spoken(due)))
    text = " ".join(p for p in parts if p)
    vars["debt_tts"] = text
    vars["had_statement_debt"] = "yes"
    return text


# ---------- offer ----------
def offer_text(vars):
    email_ok = sget(vars, "email_ok") == "yes"
    change_ok = sget(vars, "change_ok", "yes") == "yes"
    if email_ok and change_ok:
        return fill("offer_both")
    if change_ok:
        return fill("offer_change_only")
    if email_ok:
        return fill("offer_email_only")
    return ""


# ---------- cycle ----------
def week_options(vars):
    rf = (POL.get("response_fields") or {}).get("cycle_get") or {}
    lo = int(rf.get("week_min") or 1)
    hi = int(rf.get("week_max") or 4)
    cur = iget(vars, "cur_week", 0)
    return [w for w in range(lo, hi + 1) if w != cur]
