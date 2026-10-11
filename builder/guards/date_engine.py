"""Business-day calendar. Standard library only. No doubled braces in this file.

The phone-agent sandbox embeds this source. Keep every `{` isolated from the next `}`.
"""
import datetime
import json

MONTH_DAYS = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
WEEK_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ORDINALS = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth", "twenty-first", "twenty-second", "twenty-third", "twenty-fourth", "twenty-fifth", "twenty-sixth", "twenty-seventh", "twenty-eighth", "twenty-ninth", "thirtieth", "thirty-first"]


def leap(year):
    if year % 400 == 0:
        return True
    if year % 100 == 0:
        return False
    return year % 4 == 0


def make_date(year, month, day):
    if year < 1 or year > 9999 or month < 1 or month > 12 or day < 1:
        return None
    limit = MONTH_DAYS[month]
    if month == 2 and leap(year):
        limit = 29
    if day > limit:
        return None
    return datetime.date(year, month, day)


def parse_iso_date(text):
    raw = str(text or "").strip()
    if len(raw) < 10 or raw[4] != "-" or raw[7] != "-":
        return None
    if not (raw[0:4].isdigit() and raw[5:7].isdigit() and raw[8:10].isdigit()):
        return None
    return make_date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10]))


def parse_now(text):
    raw = str(text or "").strip()
    day = parse_iso_date(raw) or datetime.date(2026, 1, 1)
    hour, minute = 0, 0
    if "T" in raw:
        rest = raw.split("T", 1)[1]
        if len(rest) >= 5 and rest[2] == ":" and rest[0:2].isdigit() and rest[3:5].isdigit():
            hour = int(rest[0:2])
            minute = int(rest[3:5])
    return datetime.datetime(day.year, day.month, day.day, hour, minute)


def is_holiday(day, holidays):
    if day is None:
        return False
    iso = day.isoformat()
    for item in holidays or []:
        if str(item) == iso:
            return True
    return False


def is_business_day(day, holidays):
    if day is None or day.weekday() >= 5:
        return False
    return not is_holiday(day, holidays)


def add_business_days(today, count, holidays):
    day = today
    left = int(count)
    guard = 0
    while left > 0 and guard < 500:
        day = day + datetime.timedelta(days=1)
        guard = guard + 1
        if is_business_day(day, holidays):
            left = left - 1
    return day


def next_business_day_after(day, holidays):
    nxt = day + datetime.timedelta(days=1)
    guard = 0
    while not is_business_day(nxt, holidays) and guard < 500:
        nxt = nxt + datetime.timedelta(days=1)
        guard = guard + 1
    return nxt


def week_monday(day):
    return day - datetime.timedelta(days=day.weekday())


def weekday_index(name):
    cleaned = str(name or "").strip().lower()
    index = 0
    while index < len(WEEK_NAMES):
        if WEEK_NAMES[index] == cleaned:
            return index
        index = index + 1
    return None


def this_weekday(today, index):
    return week_monday(today) + datetime.timedelta(days=index)


def next_weekday(today, index):
    return week_monday(today) + datetime.timedelta(days=7 + index)


def last_business_day_of_week(today, holidays):
    monday = week_monday(today)
    day = monday + datetime.timedelta(days=4)
    guard = 0
    while day >= monday and not is_business_day(day, holidays) and guard < 7:
        day = day - datetime.timedelta(days=1)
        guard = guard + 1
    return day


def attempt_date(today, holidays):
    last = last_business_day_of_week(today, holidays)
    if today >= last:
        return last_business_day_of_week(today + datetime.timedelta(days=7), holidays)
    return last


def speak_date(day):
    if day is None:
        return ""
    return str(day.day) + " " + MONTH_NAMES[day.month] + " " + str(day.year)


def _month_index(token):
    cleaned = token.strip().lower().rstrip(".")
    index = 1
    while index < len(MONTH_NAMES):
        if MONTH_NAMES[index].lower() == cleaned or MONTH_NAMES[index].lower()[:3] == cleaned:
            return index
        index = index + 1
    return None


def _day_from_token(token):
    cleaned = token.strip().lower()
    if cleaned.isdigit():
        number = int(cleaned)
        if 1 <= number <= 31:
            return number
        return None
    suffix = cleaned
    for end in ("st", "nd", "rd", "th"):
        if suffix.endswith(end) and suffix[: -len(end)].isdigit():
            number = int(suffix[: -len(end)])
            if 1 <= number <= 31:
                return number
    index = 1
    while index < len(ORDINALS):
        if ORDINALS[index] == cleaned or ORDINALS[index].replace("-", " ") == cleaned:
            return index
        index = index + 1
    return None


def _contains_phrase(text, phrases):
    for phrase in phrases or []:
        if phrase and str(phrase).lower() in text:
            return True
    return False


def parse_when(text, today, vague_phrases):
    """Return (kind, date) where kind is exact, vague, or none."""
    raw = " " + str(text or "").lower().replace(",", " ").replace(".", " ") + " "
    raw = " ".join(raw.split())
    padded = " " + raw + " "
    if not raw.strip():
        return "none", None
    iso = parse_iso_date(raw.strip())
    if iso is not None:
        return "exact", iso
    if " today " in padded or padded.strip() == "today":
        return "exact", today
    if " tomorrow " in padded or padded.strip() == "tomorrow":
        return "exact", today + datetime.timedelta(days=1)
    found_week = None
    found_month = None
    found_day = None
    tokens = raw.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if found_week is None:
            found_week = weekday_index(token)
        if found_month is None:
            found_month = _month_index(token)
        if found_day is None:
            found_day = _day_from_token(token)
        if index + 1 < len(tokens):
            pair = token + " " + tokens[index + 1]
            if found_day is None:
                found_day = _day_from_token(pair)
            if found_week is None:
                found_week = weekday_index(pair)
        index = index + 1
    if found_day is None and found_week is None and _contains_phrase(padded, vague_phrases):
        return "vague", None
    if found_week is not None and found_day is None and found_month is None:
        if " next " in padded:
            return "exact", next_weekday(today, found_week)
        if " this " in padded:
            return "exact", this_weekday(today, found_week)
        candidate = this_weekday(today, found_week)
        if candidate < today:
            candidate = next_weekday(today, found_week)
        return "exact", candidate
    if found_day is not None:
        year = today.year
        month = found_month or today.month
        candidate = make_date(year, month, found_day)
        if candidate is None or (found_month is None and candidate < today):
            month = today.month + 1
            year = today.year
            if month == 13:
                month = 1
                year = year + 1
            candidate = make_date(year, month, found_day)
        if found_month is not None and candidate is not None and candidate < today:
            candidate = make_date(year + 1, found_month, found_day)
        if candidate is None:
            return "none", None
        return "exact", candidate
    if _contains_phrase(padded, vague_phrases):
        return "vague", None
    return "none", None


def parse_dob(text):
    raw = str(text or "").strip()
    iso = parse_iso_date(raw)
    if iso is not None and iso.year < 2020:
        return iso
    lowered = raw.lower().replace(",", " ")
    tokens = lowered.replace("/", " ").replace("-", " ").replace(".", " ").split()
    numbers = []
    month = None
    for token in tokens:
        if month is None:
            month = _month_index(token)
        day = _day_from_token(token)
        if token.isdigit() and len(token) == 4:
            numbers.append(int(token))
        elif day is not None and not (token.isdigit() and len(token) == 4):
            numbers.append(day)
    year = None
    parts = []
    for token in tokens:
        if token.isdigit() and len(token) == 4:
            year = int(token)
        elif token.isdigit():
            parts.append(int(token))
    if month is not None and year is not None:
        day = None
        for token in tokens:
            if _month_index(token):
                continue
            if token.isdigit() and len(token) == 4:
                continue
            day = _day_from_token(token)
            if day:
                break
        return make_date(year, month, day or 0)
    if year is not None and len(parts) >= 2:
        return make_date(year, parts[1], parts[0])
    if len(parts) >= 3:
        return make_date(parts[2], parts[1], parts[0])
    return None


def parse_clock(text):
    raw = " " + str(text or "").lower() + " "
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "midday": 12,
    }
    if " noon " in raw or " midday " in raw:
        return 12 * 60
    hour = None
    minute = 0
    tokens = raw.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if ":" in token:
            bits = token.split(":")
            if len(bits) >= 2 and bits[0].isdigit() and bits[1][:2].isdigit():
                hour = int(bits[0])
                minute = int(bits[1][:2])
        elif token.isdigit() and hour is None and int(token) <= 23:
            hour = int(token)
        elif token in words and hour is None:
            hour = words[token]
        index = index + 1
    if hour is None:
        return None
    if " pm " in raw or " p.m " in raw or " evening " in raw or " afternoon " in raw:
        if hour < 12:
            hour = hour + 12
    if (" am " in raw or " a.m " in raw or " morning " in raw) and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour * 60


def clock_text(minutes):
    hour = int(minutes) // 60
    minute = int(minutes) % 60
    return "%02d:%02d" % (hour, minute)


def band_tuple(days_late, mins, maxs, deadlines, attempts, names):
    late = int(days_late)
    index = 0
    while index < len(mins):
        if int(mins[index]) <= late <= int(maxs[index]):
            return str(names[index]), int(deadlines[index]), int(attempts[index])
        index = index + 1
    return None


def deadline_for(today, days_late, mins, maxs, deadlines, attempts, names, holidays):
    found = band_tuple(days_late, mins, maxs, deadlines, attempts, names)
    if not found:
        return None
    return add_business_days(today, found[1], holidays)


def callback_allowed(day, minutes, now, holidays, start_min, end_min):
    if day is None or minutes is None:
        return False
    if not is_business_day(day, holidays):
        return False
    today = now.date()
    allowed = []
    if is_business_day(today, holidays):
        allowed.append(today.isoformat())
        allowed.append(next_business_day_after(today, holidays).isoformat())
    else:
        allowed.append(next_business_day_after(today, holidays).isoformat())
    if day.isoformat() not in allowed:
        return False
    if minutes < int(start_min) or minutes > int(end_min):
        return False
    if day == today and minutes <= now.hour * 60 + now.minute:
        return False
    return True


def fit_named_date(named, today, deadline, holidays):
    """Return (action, date). action is ok, next_business, deadline, or reject."""
    if named is None:
        return "reject", None
    if named < today:
        return "reject", None
    if deadline is not None and named > deadline:
        return "deadline", deadline
    if not is_business_day(named, holidays):
        nxt = next_business_day_after(named, holidays)
        if deadline is not None and nxt > deadline:
            return "deadline", deadline
        return "next_business", nxt
    return "ok", named


def business_day_nth(today, count, holidays):
    return add_business_days(today, count, holidays)


def next_business_day(day, holidays):
    return next_business_day_after(day, holidays)


def resolve_weekday_phrase(phrase, now_text):
    today = parse_now(now_text).date()
    _kind, day = parse_when(phrase, today, ["next week", "end of the month", "week after next", "when my salary"])
    return day


def clamp_promise_date(iso, now_text, deadline_iso, holidays):
    today = parse_now(now_text).date()
    named = parse_iso_date(iso)
    deadline = parse_iso_date(deadline_iso)
    action, fitted = fit_named_date(named, today, deadline, holidays)
    if fitted is None:
        return "", "reject"
    if action == "ok":
        return fitted.isoformat(), "ok"
    if action == "deadline":
        return fitted.isoformat(), "after_deadline"
    return fitted.isoformat(), "shifted"


class DateEngine:
    def __init__(self, holidays, bands=None):
        self.holidays = list(holidays or [])
        self.bands = list(bands or [])

    def _band(self, days_late):
        late = int(days_late)
        for row in self.bands:
            if int(row.get("days_late_min")) <= late <= int(row.get("days_late_max")):
                return row
        return None

    def deadline_for(self, now_text, days_late):
        row = self._band(days_late)
        if not row:
            return ""
        today = parse_now(now_text).date()
        return add_business_days(today, int(row.get("deadline_business_days")), self.holidays).isoformat()

    def attempt1_date(self, now_text):
        today = parse_now(now_text).date()
        return attempt_date(today, self.holidays).isoformat()


def apply_facts(vars):
    holidays = vars.get("policy_holidays") or []
    if isinstance(holidays, str):
        try:
            holidays = json.loads(holidays)
        except Exception:
            holidays = []
    bands = vars.get("policy_bands") or []
    if isinstance(bands, str):
        try:
            bands = json.loads(bands)
        except Exception:
            bands = []
    floors_of = vars.get("floor_fields")
    if floors_of in (None, "", {}):
        floors_of = vars.get("floor_fields_json") or {}
    if isinstance(floors_of, str):
        try:
            floors_of = json.loads(floors_of)
        except Exception:
            floors_of = {}
    if not isinstance(floors_of, dict):
        floors_of = {}
    now = parse_now(str(vars.get("now") or ""))
    today = now.date()
    raw = vars.get("account_products_json")
    if not raw:
        raw = vars.get("get_account_response.data.products")
    if not raw:
        # Freya often stores tool fields as dotted keys, not a nested list.
        dotted = []
        index = 0
        while index < 8:
            pid = vars.get("get_account_response.data.products." + str(index) + ".product_id")
            if pid in (None, ""):
                break
            row = {
                "product_id": pid,
                "type": vars.get("get_account_response.data.products." + str(index) + ".type"),
                "past_due": vars.get("get_account_response.data.products." + str(index) + ".past_due"),
                "days_late": vars.get("get_account_response.data.products." + str(index) + ".days_late"),
                "instalment_amount": vars.get(
                    "get_account_response.data.products." + str(index) + ".instalment_amount"
                ),
                "minimum_payment": vars.get(
                    "get_account_response.data.products." + str(index) + ".minimum_payment"
                ),
                "card_last4": vars.get("get_account_response.data.products." + str(index) + ".card_last4"),
            }
            dotted.append(row)
            index = index + 1
        if dotted:
            raw = dotted
    rows = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, str) and raw.strip().startswith("["):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = []
        if isinstance(parsed, list):
            rows = parsed
    lines = []
    summaries = []
    ids = []
    deadlines = {}
    floors = {}
    dues = {}
    attempts_map = {}
    labels = {}
    index = 0
    while index < len(rows):
        raw_row = rows[index] if isinstance(rows[index], dict) else {}
        pid = str(raw_row.get("product_id") or ("p" + str(index + 1)))
        ptype = str(raw_row.get("type") or "")
        try:
            past_due = float(raw_row.get("past_due") or 0)
        except Exception:
            past_due = 0.0
        try:
            days_late = int(float(raw_row.get("days_late") or 0))
        except Exception:
            days_late = 0
        field = str(floors_of.get(ptype) or "")
        floor_raw = raw_row.get(field) if field else None
        try:
            floor = float(floor_raw if floor_raw is not None else past_due)
        except Exception:
            floor = past_due
        deadline = today
        attempts = 1
        band_name = ""
        for row in bands:
            if int(row.get("days_late_min") or 0) <= days_late <= int(row.get("days_late_max") or 0):
                band_name = str(row.get("name") or "")
                attempts = int(row.get("persuasion_attempts") or row.get("attempts") or 1)
                deadline = add_business_days(today, int(row.get("deadline_business_days") or 0), holidays)
                break
        last4 = str(raw_row.get("card_last4") or "")
        if ptype == "credit_card":
            label = "credit card ending " + last4
        else:
            label = ptype.replace("_", " ") or "account"
        deadlines[pid] = deadline.isoformat()
        floors[pid] = floor
        dues[pid] = past_due
        attempts_map[pid] = attempts
        labels[pid] = label
        ids.append(pid)
        fact = pid + " " + label + " past_due " + ("%.2f" % past_due) + " floor " + ("%.2f" % floor)
        fact = fact + " days_late " + str(days_late) + " band " + band_name
        fact = fact + " deadline " + deadline.isoformat() + " attempts " + str(attempts)
        lines.append(fact)
        summaries.append("The " + label + " is " + ("%.2f" % past_due) + " past due and " + str(days_late) + " days late.")
        index = index + 1
    vars["allowed_product_ids"] = ids
    vars["deadlines_json"] = json.dumps(deadlines)
    vars["floors_json"] = json.dumps(floors)
    vars["past_dues_json"] = json.dumps(dues)
    vars["attempts_json"] = json.dumps(attempts_map)
    vars["labels_json"] = json.dumps(labels)
    if not str(vars.get("persuasion_used_json") or "").strip():
        vars["persuasion_used_json"] = "{}"
    a1 = attempt_date(today, holidays)
    vars["attempt1_date"] = a1.isoformat()
    vars["attempt1_spoken"] = speak_date(a1)
    if ids:
        vars["primary_product_id"] = ids[0]
        vars["primary_floor"] = str(floors.get(ids[0], ""))
        vars["primary_past_due"] = str(dues.get(ids[0], ""))
    shift_lines = []
    for h in holidays or []:
        hd = parse_iso_date(str(h))
        if hd is None or hd < today:
            continue
        # Only hint holidays that fall before the farthest product deadline.
        farthest = today
        for iso in deadlines.values():
            dline = parse_iso_date(str(iso))
            if dline is not None and dline > farthest:
                farthest = dline
        if hd > farthest:
            continue
        nxt = next_business_day_after(hd, holidays)
        shift_lines.append(
            str(h) + " is a holiday — offer " + nxt.isoformat() + " (next business day), not the band deadline"
        )
    facts = "\n".join(lines)
    facts = facts + "\nAttempt-1 date " + a1.isoformat() + " (" + speak_date(a1) + ")"
    if shift_lines:
        facts = facts + "\n" + "\n".join(shift_lines)
    vars["date_facts"] = facts
    if summaries:
        vars["account_summary"] = " ".join(summaries)
    else:
        vars["account_summary"] = "No late products were returned."
    # Brief A 17/28, Brief B 2/4: state every product, then ONE first ask — spoken by TTS on
    # collection entry so the dialogue model cannot ask for a payment before listing products.
    intro = str(vars.get("collections_intro") or "I am calling from the collections team about a late payment.")
    ask_one = str(vars.get("first_ask") or "Can you make this payment today?")
    ask_all = str(vars.get("first_ask_all") or "Can you pay all of them today?")
    vars["intro_tts"] = intro + " " + vars["account_summary"] + " " + (ask_all if len(ids) > 1 else ask_one)
    if not ids:
        vars["dates_ready"] = "no"
    else:
        vars["dates_ready"] = "yes"


def build_date_engine_code():
    import inspect
    needed = [
        leap, make_date, parse_iso_date, parse_now, is_holiday, is_business_day,
        add_business_days, next_business_day_after, week_monday, last_business_day_of_week,
        attempt_date, speak_date, apply_facts,
    ]
    chunks = [
        "import datetime",
        "import json",
        "MONTH_DAYS = " + json.dumps(MONTH_DAYS),
        "MONTH_NAMES = " + json.dumps(MONTH_NAMES),
    ]
    for fn in needed:
        chunks.append(inspect.getsource(fn))
    # Fail soft: leave empty maps so write gates fail closed instead of crashing the call.
    chunks.append(
        "try:\n"
        "    apply_facts(vars)\n"
        "    vars['dates_ready'] = 'yes'\n"
        "except Exception:\n"
        "    vars['dates_ready'] = 'no'\n"
        "    vars['deadlines_json'] = '{}'\n"
        "    vars['floors_json'] = '{}'\n"
        "    vars['past_dues_json'] = '{}'\n"
        "    vars['attempts_json'] = '{}'\n"
        "    vars['labels_json'] = '{}'\n"
        "    vars['allowed_product_ids'] = []\n"
        "    vars['date_facts'] = ''\n"
        "    vars['account_summary'] = 'Date facts could not be computed.'\n"
    )
    text = "\n".join(chunks)
    if "{{" in text or "}}" in text:
        raise RuntimeError("DATE_ENGINE_CODE has doubled braces")
    return text


DATE_ENGINE_CODE = build_date_engine_code()
