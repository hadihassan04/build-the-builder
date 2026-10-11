"""Parse a markdown service contract (or an OpenAPI file) into a services IR.

Generic: service names are taken from headings that contain a backticked name next to a
request/response pair of JSON code fences; the error table from any markdown table whose
header row names an HTTP column and a code column; the base URL from the first
```-fenced or inline https:// address that ends with a slash or a version segment.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def _json_blocks(text: str) -> List[Dict[str, Any]]:
    """All fenced code blocks with their start offset; parsed as JSON when possible."""
    out = []
    for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.S):
        body = m.group(2)
        parsed = None
        try:
            parsed = json.loads(body)
        except Exception:
            # tolerate "..." placeholders like  "...": "as in section 3"
            try:
                cleaned = re.sub(r'"\.\.\.":\s*"[^"]*",?', "", body)
                cleaned = re.sub(r",\s*}", "}", cleaned)
                parsed = json.loads(cleaned)
            except Exception:
                parsed = None
        out.append({"start": m.start(), "lang": m.group(1), "text": body, "json": parsed})
    return out


def _headings(text: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r"^(#{1,6})\s+(.*?)\s*$", text, re.M):
        out.append({"start": m.start(), "level": len(m.group(1)), "title": m.group(2)})
    return out


def _base_url(text: str) -> str:
    m = re.search(r"https?://[\w.\-]+(?:/[\w.\-]+)*/(?:v\d+/?)", text)
    if m:
        return m.group(0).rstrip("/")
    m = re.search(r"https?://[\w.\-]+(?:/[\w.\-]+)*", text)
    return m.group(0).rstrip("/") if m else ""


def _error_table(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header = [c.strip().strip("`").lower() for c in line.strip().strip("|").split("|")]
        if not any(h.startswith("http") for h in header) or not any("code" in h for h in header):
            continue
        hi = next(j for j, h in enumerate(header) if h.startswith("http"))
        ci = next(j for j, h in enumerate(header) if "code" in h)
        ti = next((j for j, h in enumerate(header) if h.startswith("type")), None)
        wi = next((j for j, h in enumerate(header) if h.startswith("when") or "message" in h or "mean" in h), None)
        si = next((j for j, h in enumerate(header) if "service" in h), None)
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
            if len(cells) >= 2:
                try:
                    http = int(re.sub(r"\D", "", cells[hi]) or 0)
                except Exception:
                    http = 0
                code = cells[ci].strip("`")
                if http:
                    msg = ""
                    if wi is not None and wi < len(cells):
                        mm = re.search(r"`\"?([^`]*?)\"?`", cells[wi])
                        if mm and ("message" in cells[wi].lower() or "say" in cells[wi].lower() or '"' in cells[wi]):
                            msg = mm.group(1)
                    services: List[str] = []
                    if si is not None and si < len(cells):
                        services = re.findall(r"`([^`]+)`", cells[si]) or ([] if "all" in cells[si].lower() else [])
                        all_flag = "all" in cells[si].lower() and not services
                    else:
                        all_flag = True
                    rows.append({
                        "http": http,
                        "code": code if code and not code.startswith("(") else "",
                        "type": (cells[ti].strip("`") if ti is not None and ti < len(cells) else ""),
                        "message": msg,
                        "services": services,
                        "all": all_flag,
                    })
            j += 1
        if rows:
            break
    return rows


def parse_contract_markdown(text: str) -> Dict[str, Any]:
    blocks = _json_blocks(text)
    heads = _headings(text)
    base = _base_url(text)
    errors = _error_table(text)
    services: Dict[str, Any] = {}
    order: List[str] = []
    # candidate service headings: contain a backticked token with a dot or underscore
    for idx, h in enumerate(heads):
        m = re.search(r"`([\w][\w.\-]*[\w])`", h["title"])
        if not m:
            continue
        name = m.group(1)
        if "." not in name and "_" not in name:
            continue
        end = heads[idx + 1]["start"] if idx + 1 < len(heads) else len(text)
        section = [b for b in blocks if h["start"] < b["start"] < end]
        req = None
        resps = []
        for b in section:
            j = b["json"]
            if not isinstance(j, dict):
                continue
            if "result" not in j and "data" in j and req is None:
                req = j
            elif "result" in j or ("data" in j and req is not None):
                resps.append(j)
        if req is None and not resps:
            continue
        sec_text = text[h["start"]:end]
        svc_errors = []
        for e in errors:
            if e["all"] or name in e["services"]:
                svc_errors.append(e)
        for code_m in re.finditer(r"(\d{3})\s+`([A-Z_0-9]+)`", sec_text):
            http, code = int(code_m.group(1)), code_m.group(2)
            if not any(e["http"] == http and e["code"] == code for e in svc_errors):
                row = next((e for e in errors if e["code"] == code), None)
                svc_errors.append(row or {"http": http, "code": code, "type": "", "message": "", "services": [name], "all": False})
        data_req = req.get("data") if isinstance(req, dict) else {}
        envelope_extra = {k: v for k, v in (req or {}).items() if k != "data"}
        success = next((r for r in resps if isinstance(r.get("data"), dict) or isinstance(r.get("data"), list)), None)
        error_examples = [r for r in resps if "data" not in r]
        services[name] = {
            "name": name,
            "method": "POST",
            "path": "/" + name,
            "url": (base + "/" + name) if base else name,
            "request_data_example": data_req if isinstance(data_req, dict) else {},
            "request_fields": sorted(list(data_req.keys())) if isinstance(data_req, dict) else [],
            "request_envelope_extra": envelope_extra,
            "response_data_example": (success or {}).get("data") if success else None,
            "response_result_example": (success or {}).get("result") if success else None,
            "error_examples": error_examples,
            "errors": svc_errors,
            "summary": h["title"],
        }
        order.append(name)
    # explicit path overrides like `POST https://host/voice/v1/cards.list`
    for name in order:
        m = re.search(r"https?://[\w.\-/]*/" + re.escape(name) + r"\b", text)
        if m:
            services[name]["url"] = m.group(0)
            services[name]["path"] = m.group(0)[len(base):] if base and m.group(0).startswith(base) else "/" + name
    # any error example anywhere (for templates)
    error_template = None
    for b in blocks:
        j = b["json"]
        if isinstance(j, dict) and "result" in j and "data" not in j and isinstance(j["result"], dict):
            error_template = j
            break
    success_template = None
    for b in blocks:
        j = b["json"]
        if isinstance(j, dict) and isinstance(j.get("result"), dict) and "data" in j:
            success_template = j["result"]
            break
    date_fmt = ""
    m = re.search(r"`(dd[./-]MM[./-]yyyy)`", text)
    if m:
        date_fmt = m.group(1)
    return {
        "kind": "markdown-contract",
        "base_url": base,
        "names": order,
        "services": services,
        "errors": errors,
        "error_template": error_template,
        "success_result_template": success_template,
        "date_format": date_fmt or "dd.MM.yyyy",
        "request_envelope": {"data_key": "data", "extra": next((services[n]["request_envelope_extra"] for n in order if services[n]["request_envelope_extra"]), {})},
        "response_envelope": {"data_key": "data", "result_key": "result"},
    }


def parse_openapi_file(path: str) -> Dict[str, Any]:
    """Compatibility: wrap builder.tools_parse output in the same IR shape."""
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools_parse import parse_openapi  # type: ignore
    t = parse_openapi(path)
    services = {}
    for n in t["names"]:
        s = t["by_name"][n]
        services[n] = {
            "name": n, "method": s["method"], "path": s["path"], "url": s["url"],
            "request_data_example": {k: "" for k in s["fields"]}, "request_fields": list(s["fields"].keys()),
            "request_envelope_extra": {}, "response_data_example": s.get("response_properties"),
            "response_result_example": None, "error_examples": [], "errors": [], "summary": s.get("summary", ""),
            "openapi_fields": s["fields"], "required": s.get("required", []),
        }
    return {"kind": "openapi", "base_url": t["base_url"], "names": t["names"], "services": services,
            "errors": [], "error_template": None, "success_result_template": None, "date_format": "",
            "request_envelope": {"data_key": None, "extra": {}}, "response_envelope": {"data_key": "data", "result_key": None}}


def error_body(ir: Dict[str, Any], http: int, code: str, message: str, etype: str = "ERROR", guid: str = "00000000000000000000000000000000") -> Dict[str, Any]:
    """Render an error body in the contract's envelope shape."""
    tmpl = ir.get("error_template")
    if not tmpl:
        return {"error": code or "error", "message": message, "status": http}
    body = json.loads(json.dumps(tmpl))
    res = body.get("result", {})
    for k in list(res.keys()):
        v = res[k]
        if k in ("returnCode", "code"):
            res[k] = code
        elif k in ("messageText", "message"):
            res[k] = message
        elif k == "type":
            res[k] = etype
        elif k == "guid":
            res[k] = guid
        elif isinstance(v, dict) and "guid" in v:
            v["guid"] = guid
    return body


def mock_fallbacks(ir: Dict[str, Any]) -> Dict[str, Any]:
    """Per-service not-found response: the service's 404 if the contract lists one, else a 400 invalid-arguments body."""
    out: Dict[str, Any] = {}
    generic400 = next((e for e in ir.get("errors", []) if e["http"] == 400), None)
    g_code = generic400["code"] if generic400 else "INVALID_ARGUMENTS_ERROR"
    g_type = (generic400.get("type") if generic400 else "") or "ERROR"
    for n in ir["names"]:
        e404 = next((e for e in ir["services"][n]["errors"] if e["http"] == 404), None)
        if e404:
            msg = e404.get("message") or "not found"
            # prefer an example body in the section
            ex = next((x for x in ir["services"][n]["error_examples"] if str(x.get("result", {}).get("code")) == e404["code"]), None)
            out[n] = {"http": 404, "body": ex or error_body(ir, 404, e404["code"], msg, e404.get("type") or "WARNING")}
        else:
            out[n] = {"http": 400, "body": error_body(ir, 400, g_code, "invalid arguments", g_type)}
    out["*"] = {"http": 400, "body": error_body(ir, 400, g_code, "invalid arguments", g_type)}
    return out


def service_url(ir: Dict[str, Any], name: str, mock_url: Optional[str]) -> str:
    s = ir["services"][name]
    if mock_url:
        path = s.get("path") or ("/" + name)
        base = ir.get("base_url", "")
        # keep the contract's path under the base host, e.g. /voice/v1/<name>
        m = re.match(r"https?://[^/]+(/.*)?$", base)
        prefix = (m.group(1) or "") if m else ""
        full_path = prefix.rstrip("/") + "/" + name if prefix else path
        return mock_url.rstrip("/") + full_path
    return s["url"]


if __name__ == "__main__":
    import sys
    ir = parse_contract_markdown(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps({"base_url": ir["base_url"], "names": ir["names"], "date_format": ir["date_format"],
                      "errors": ir["errors"], "svc": {n: {"url": s["url"], "req": s["request_fields"], "errors": [(e["http"], e["code"]) for e in s["errors"]], "has_resp": s["response_data_example"] is not None} for n, s in ir["services"].items()}}, indent=1, ensure_ascii=False))
    print(json.dumps(mock_fallbacks(ir), ensure_ascii=False)[:1500])
