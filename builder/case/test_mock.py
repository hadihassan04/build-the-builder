"""Offline exact-match test runner for the generated mock Worker.

    python3 builder/case/test_mock.py <test-customers.json> <worker.js>
        [--request-fields fields.json] [--path-prefix /voice/v1]
        [--harness mock/test/run_worker.mjs]

For EVERY leaf of the test data a POST request is built with the lookup
fields reconstructed, sent to the worker module under Node (which has
global Request/Response), and the status and parsed JSON body are compared
with deep equality. Extra cases check not-found behaviour (unknown
customer, unknown card, missing week, template placeholder, unknown
service), numeric-vs-string key equivalence, zero-stripping, the health
endpoint and non-POST 404. Delays are skipped by setting
``globalThis.MOCK_NO_DELAY = true`` in the harness.

Request field names
-------------------
The data file only knows *keys* (customer id, card number, week); the
*request field names* come from the service contract. ``build_requests``
takes ``request_fields = {service: [field, ...]}`` (the contract's request
fields per service) and picks, for each key level, the first field whose name
contains ``customer`` / ``card`` / ``week`` (case-insensitive) according to
what the key looks like (a long all-digit string -> customer, a 1-2 digit
string -> week, anything else such as a masked number -> card). Only when
``request_fields`` is ``None`` (or has no fitting field) do the defaults
``customerNo`` / ``cardNumber`` / ``newStatementWeek`` apply; the builder is
expected to pass the real names from the contract.

The Node harness ``mock/test/run_worker.mjs`` is (re)generated on every run;
cases are streamed to it on stdin so no data file is left in the repo.
Prints ``mock tests: N/N passed`` and exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:  # run as a script from anywhere or imported as builder.case.test_mock
    from .mockgen import DEFAULT_LAYOUT, Layout, iter_leaves, normalize_fallbacks  # type: ignore
except ImportError:  # pragma: no cover - script mode
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mockgen import DEFAULT_LAYOUT, Layout, iter_leaves, normalize_fallbacks  # type: ignore

__all__ = ["build_requests", "build_extra_cases", "run_cases", "main"]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HARNESS = REPO_ROOT / "mock" / "test" / "run_worker.mjs"

DEFAULT_FIELD_NAMES = {"customer": "customerNo", "card": "cardNumber", "week": "newStatementWeek"}
DEFAULT_CALL = {"ani": "", "lang": "tr"}


# --------------------------------------------------------------------------
# Request construction
# --------------------------------------------------------------------------


def classify_key(key: str, level: int) -> str:
    """Classify a lookup key as ``customer`` / ``card`` / ``week``.

    Level 0 is always the customer id. Below that: 1-2 digit strings are
    weeks, everything else (masked numbers etc.) is a card.
    """
    if level == 0:
        return "customer"
    k = key.strip()
    if k.isdigit() and len(k) <= 2:
        return "week"
    return "card"


def field_name_for(kind: str, service: str, request_fields: dict[str, list[str]] | None) -> str:
    fields = (request_fields or {}).get(service) or []
    for f in fields:
        if kind in f.lower():
            return f
    return DEFAULT_FIELD_NAMES[kind]


def _request_data(service: str, chain: list[str], request_fields: dict[str, list[str]] | None) -> dict:
    """Request ``data`` carrying the lookup keys. Customer-level services send
    the customer id; nested services send only the nested keys (as the real
    flow would, e.g. card number + week)."""
    data: dict[str, Any] = {}
    if len(chain) == 1:
        data[field_name_for("customer", service, request_fields)] = chain[0]
        return data
    for level, key in enumerate(chain):
        if level == 0:
            continue
        data[field_name_for(classify_key(key, level), service, request_fields)] = key
    return data


def _envelope(data: dict, call: dict | None) -> dict:
    return {"data": data, "call": copy.deepcopy(call if call is not None else DEFAULT_CALL)}


def build_requests(
    test_data: dict,
    request_fields: dict[str, list[str]] | None = None,
    *,
    path_prefix: str = "/voice/v1",
    call: dict | None = None,
    layout: Layout = DEFAULT_LAYOUT,
) -> list[dict]:
    """One exact-match case per leaf of ``test_data``.

    Each case: ``{"name", "method": "POST", "path", "body", "expect":
    {"status", "body"}, "delay": bool}``.
    """
    cases = []
    for cid, service, nested, leaf in iter_leaves(test_data, layout):
        chain = [cid] + nested
        data = _request_data(service, chain, request_fields)
        cases.append(
            {
                "name": f"{service} {'/'.join(chain)}",
                "method": "POST",
                "path": f"{path_prefix.rstrip('/')}/{service}",
                "body": _envelope(data, call),
                "expect": {"status": leaf[layout.status_key], "body": leaf.get(layout.body_key)},
                "delay": bool(leaf.get(layout.delay_key)),
            }
        )
    return cases


def _absent_key(existing: set[str], sample: str) -> str:
    """A key shaped like ``sample`` that is not in ``existing``."""
    for i in range(len(sample) - 1, -1, -1):
        ch = sample[i]
        if not ch.isdigit():
            continue
        for d in "0123456789":
            if d == ch:
                continue
            cand = sample[:i] + d + sample[i + 1 :]
            if cand not in existing:
                return cand
    cand = sample + "9"
    while cand in existing:
        cand += "9"
    return cand


def _fallback_expect(fallbacks: dict, service: str | None) -> dict:
    fb = fallbacks.get(service) if service else None
    if fb is None:
        fb = fallbacks["*"]
    return {"status": fb["http"], "body": fb["body"]}


def build_extra_cases(
    test_data: dict,
    fallbacks: dict,
    request_fields: dict[str, list[str]] | None = None,
    *,
    path_prefix: str = "/voice/v1",
    health_path: str = "/health",
    call: dict | None = None,
    layout: Layout = DEFAULT_LAYOUT,
) -> list[dict]:
    """Not-found, equivalence, health and routing cases."""
    prefix = path_prefix.rstrip("/")
    by_depth: dict[str, int] = {}
    first_leaf: dict[str, tuple[list[str], dict]] = {}
    keys_at: dict[tuple[str, int], set[str]] = {}
    nodes: dict[tuple[str, str], set[str]] = {}  # (service, parent chain) -> keys
    customer_ids: set[str] = set()
    owners: list[tuple[str, str, list[str], dict]] = []
    for cid, service, nested, leaf in iter_leaves(test_data, layout):
        chain = [cid] + nested
        customer_ids.add(cid)
        owners.append((cid, service, nested, leaf))
        by_depth[service] = len(chain)
        first_leaf.setdefault(service, (chain, leaf))
        for level, key in enumerate(chain):
            keys_at.setdefault((service, level), set()).add(key)
        nodes.setdefault((service, "/".join(chain[:-1])), set()).add(chain[-1])

    cases: list[dict] = []

    def post(name: str, service: str, data: Any, expect: dict, raw_body: Any = None) -> None:
        cases.append(
            {
                "name": name,
                "method": "POST",
                "path": f"{prefix}/{service}",
                "body": raw_body if raw_body is not None else _envelope(data, call),
                "expect": expect,
                "delay": False,
            }
        )

    for service, depth in by_depth.items():
        chain, leaf = first_leaf[service]
        fb = _fallback_expect(fallbacks, service)
        if depth == 1:
            fname = field_name_for("customer", service, request_fields)
            unknown = _absent_key(customer_ids, chain[0])
            post(f"{service}: unknown customer -> fallback", service, {fname: unknown}, fb)
            post(f"{service}: template placeholder -> fallback", service, {fname: "{{customerNumber}}"}, fb)
            post(f"{service}: empty data -> fallback", service, {}, fb)
            if chain[0].isdigit():
                ok = {"status": leaf[layout.status_key], "body": leaf.get(layout.body_key)}
                post(f"{service}: customer id as number", service, {fname: int(chain[0])}, ok)
                post(f"{service}: customer id zero-padded", service, {fname: "0" + chain[0]}, ok)
                post(f"{service}: customer id with spaces", service, {fname: f" {chain[0]} "}, ok)
            continue
        # nested services: unknown level-1 key (e.g. unknown card)
        level1 = keys_at[(service, 1)]
        k1name = field_name_for(classify_key(chain[1], 1), service, request_fields)
        data = _request_data(service, chain, request_fields)
        bad = dict(data)
        bad[k1name] = _absent_key(level1, chain[1])
        post(f"{service}: unknown {k1name} -> fallback", service, bad, fb)
        tmpl = dict(data)
        tmpl[k1name] = "{{cardNumber}}"
        post(f"{service}: template placeholder -> fallback", service, tmpl, fb)
        if depth >= 3:
            # a leaf-level key that exists for the service but not under this parent
            parent = "/".join(chain[:-1])
            present = nodes[(service, parent)]
            all_last = keys_at[(service, depth - 1)]
            missing = sorted(all_last - present)
            klast = field_name_for(classify_key(chain[-1], depth - 1), service, request_fields)
            if missing:
                miss = dict(data)
                miss[klast] = missing[0]
                post(f"{service}: missing {klast}={missing[0]} for {chain[1]} -> fallback", service, miss, fb)
            if chain[-1].isdigit():
                num = dict(data)
                num[klast] = int(chain[-1])
                post(
                    f"{service}: {klast} as number",
                    service,
                    num,
                    {"status": leaf[layout.status_key], "body": leaf.get(layout.body_key)},
                )
        # a nested service also works when the customer id is sent along
        with_cust = dict(data)
        with_cust[field_name_for("customer", service, request_fields)] = chain[0]
        post(
            f"{service}: with customer id as well",
            service,
            with_cust,
            {"status": leaf[layout.status_key], "body": leaf.get(layout.body_key)},
        )
        # a known customer id together with another customer's key -> fallback (never cross customers)
        other_owner = next((c for c, s_, n_, _l in owners if s_ == service and c != chain[0] and n_), None)
        if other_owner is not None:
            wrong = dict(data)
            wrong[field_name_for("customer", service, request_fields)] = other_owner
            post(f"{service}: other customer's id with this key -> fallback", service, wrong, fb)

    # routing / envelope
    any_service = next(iter(by_depth))
    post("unknown service -> default fallback", "no.such.service", {}, _fallback_expect(fallbacks, None))
    post("invalid JSON body -> fallback", any_service, None, _fallback_expect(fallbacks, any_service), raw_body="not json")
    chain, leaf = first_leaf[any_service]
    cases.append(
        {
            "name": f"{any_service}: any path prefix",
            "method": "POST",
            "path": f"/other/prefix/{any_service}",
            "body": _envelope(_request_data(any_service, chain, request_fields), call),
            "expect": {"status": leaf[layout.status_key], "body": leaf.get(layout.body_key)},
            "delay": False,
        }
    )
    cases.append(
        {
            "name": "GET health",
            "method": "GET",
            "path": health_path,
            "body": None,
            "expect": {"status": 200, "body": {"ok": True, "services": sorted(by_depth), "customers": len(customer_ids)}},
            "delay": False,
            "compare": "health",
        }
    )
    cases.append(
        {
            "name": "GET unknown -> 404",
            "method": "GET",
            "path": "/nope",
            "body": None,
            "expect": {"status": 404, "body": {"error": "not_found"}},
            "delay": False,
        }
    )
    cases.append({"name": "OPTIONS preflight", "method": "OPTIONS", "path": f"{prefix}/{any_service}", "body": None, "expect": {"status": 204, "body": None}, "delay": False, "compare": "status"})
    return cases


# --------------------------------------------------------------------------
# Node harness
# --------------------------------------------------------------------------

_HARNESS = r"""// GENERATED by builder/case/test_mock.py - offline harness for the mock worker.
// usage: node run_worker.mjs <worker.js>   (cases JSON on stdin)
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

globalThis.MOCK_NO_DELAY = true; // the worker skips delaySeconds when this is set

const workerPath = process.argv[2];
if (!workerPath) { console.error("usage: node run_worker.mjs <worker.js> < cases.json"); process.exit(2); }
const mod = await import(pathToFileURL(resolve(workerPath)).href);
const worker = mod.default;
const cases = JSON.parse(readFileSync(0, "utf8"));

function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (typeof a === "object") {
    if (Array.isArray(b)) return false;
    const ka = Object.keys(a), kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]));
  }
  return false;
}

const summarize = (v) => { const s = JSON.stringify(v); return s && s.length > 300 ? s.slice(0, 300) + "..." : s; };

let passed = 0;
const failures = [];
const t0 = Date.now();
for (const c of cases) {
  const init = { method: c.method, headers: {} };
  if (c.body !== null && c.body !== undefined) {
    init.body = typeof c.body === "string" ? c.body : JSON.stringify(c.body);
    init.headers["content-type"] = "application/json";
  }
  const req = new Request("http://mock" + c.path, init);
  let status, text, body, parseError = null, headers = {};
  try {
    const res = await worker.fetch(req, { }, { waitUntil() {}, passThroughOnException() {} });
    status = res.status;
    text = await res.text();
    headers = Object.fromEntries(res.headers.entries());
    if (c.compare !== "status") {
      try { body = JSON.parse(text); } catch (e) { parseError = String(e); }
    }
  } catch (e) {
    failures.push({ name: c.name, error: String(e && e.stack || e) });
    continue;
  }
  const problems = [];
  if (status !== c.expect.status) problems.push(`status ${status} != ${c.expect.status}`);
  if (c.compare === "status") {
    // status only
  } else if (parseError) {
    problems.push(`body not JSON: ${parseError}`);
  } else if (c.compare === "health") {
    if (!(body && body.ok === true && Array.isArray(body.services) && deepEqual([...body.services].sort(), c.expect.body.services) && body.customers === c.expect.body.customers)) problems.push(`health body ${summarize(body)} != ${summarize(c.expect.body)}`);
  } else if (!deepEqual(body, c.expect.body)) {
    problems.push(`body ${summarize(body)} != ${summarize(c.expect.body)}`);
  }
  if (c.method === "POST" && !/^application\/json/.test(headers["content-type"] || "")) problems.push(`content-type ${headers["content-type"]}`);
  if (problems.length) failures.push({ name: c.name, problems }); else passed++;
}
const result = { total: cases.length, passed, failed: failures.length, ms: Date.now() - t0, failures: failures.slice(0, 50) };
console.log(JSON.stringify(result));
process.exit(failures.length ? 1 : 0);
"""


def write_harness(path: Path = DEFAULT_HARNESS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_HARNESS, encoding="utf-8")
    return path


def run_cases(worker_js: Path, cases: list[dict], harness: Path = DEFAULT_HARNESS, node: str = "node") -> dict:
    """Run ``cases`` against ``worker_js`` under Node; return the result dict."""
    if shutil.which(node) is None:
        raise RuntimeError(f"{node!r} not found on PATH")
    write_harness(harness)
    proc = subprocess.run(
        [node, str(harness), str(worker_js)],
        input=json.dumps(cases, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=600,
    )
    out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    if not out:
        raise RuntimeError(f"harness produced no output (exit {proc.returncode}):\n{proc.stderr.decode('utf-8', 'replace')}")
    try:
        result = json.loads(out[-1])
    except json.JSONDecodeError:
        raise RuntimeError(f"harness output not JSON (exit {proc.returncode}):\n{proc.stdout.decode()}\n{proc.stderr.decode()}")
    result["stderr"] = proc.stderr.decode("utf-8", "replace")
    return result


def _fallbacks_from_worker(worker_js: Path) -> dict:
    """Read the FALLBACKS literal back out of a generated worker so the
    not-found cases expect exactly what the worker is configured with."""
    text = worker_js.read_text(encoding="utf-8")
    marker = "const FALLBACKS = "
    start = text.index(marker) + len(marker)
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text, start)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline exact-match tests for the generated mock worker.")
    parser.add_argument("test_json")
    parser.add_argument("worker_js")
    parser.add_argument("--request-fields", help="JSON {service: [request field names]} from the contract")
    parser.add_argument("--fallbacks", help="JSON fallbacks used to generate the worker (default: read from the worker)")
    parser.add_argument("--path-prefix", default="/voice/v1")
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--harness", default=str(DEFAULT_HARNESS), help="where to write the Node harness")
    parser.add_argument("--leaves-only", action="store_true", help="skip the extra not-found/equivalence cases")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    with open(args.test_json, encoding="utf-8") as fh:
        test_data = json.load(fh)
    request_fields = None
    if args.request_fields:
        with open(args.request_fields, encoding="utf-8") as fh:
            request_fields = json.load(fh)
    worker_js = Path(args.worker_js)

    cases = build_requests(test_data, request_fields, path_prefix=args.path_prefix)
    n_leaves = len(cases)
    if not args.leaves_only:
        if args.fallbacks:
            with open(args.fallbacks, encoding="utf-8") as fh:
                fallbacks = normalize_fallbacks(json.load(fh))
        else:
            fallbacks = _fallbacks_from_worker(worker_js)
        cases += build_extra_cases(
            test_data, fallbacks, request_fields, path_prefix=args.path_prefix, health_path=args.health_path
        )

    try:
        result = run_cases(worker_js, cases, Path(args.harness))
    except RuntimeError as exc:
        print(f"mock tests: ERROR {exc}", file=sys.stderr)
        return 1

    for f in result.get("failures", []):
        print(f"FAIL {f.get('name')}: {f.get('problems') or f.get('error')}", file=sys.stderr)
    if args.verbose and result.get("stderr"):
        print(result["stderr"], file=sys.stderr)
    extra = result["total"] - n_leaves
    print(
        f"mock tests: {result['passed']}/{result['total']} passed "
        f"({n_leaves} leaves + {extra} extra cases, {result.get('ms', 0)} ms)"
    )
    return 0 if result["failed"] == 0 and result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
