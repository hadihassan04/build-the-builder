"""Generate a Cloudflare Worker (ES module) that mocks a bank's services
exactly (status + body) from a test-data JSON file.

The generator is generic: nothing about a particular bank (service names,
customer numbers, card numbers, request field names, message strings) is
hard-coded here. Everything is read from the data file or passed in as a
parameter.

Data-file layout (the *file* schema, not the bank's API; every key name is a
parameter of ``Layout`` with the defaults below)::

    {"description": str,
     "customers": [
        {"customerNo": "90000001", ..., "services": {
            "<service>": <node>, ...}}]}

A ``<node>`` is either a *leaf* ``{"http": int, "response": <body>,
"delaySeconds"?: int}`` or a dict of ``{"<key>": <node>}`` nested to any
depth (e.g. card number, then statement week).

Lookup performed by the generated worker (per request, per service):

1. Collect candidate values from the request ``data`` object (scalar values,
   plus scalar values of nested objects / arrays one level down). Values are
   compared as trimmed strings; numbers and strings are equal when their
   string forms match (``3 == "3"``), leading zeros are ignored for all-digit
   values, inner whitespace is ignored, ``"3.0"`` equals ``"3"``. Values that
   are unfilled template placeholders (``"{{var}}"``) never match.
2. Customer level: any candidate equal to a customer id selects that
   customer's node for the service. If no candidate matches a customer id,
   every customer's node is tried and the request succeeds only when exactly
   one customer yields a leaf (card-keyed services carry no customer id; card
   keys are expected to be unique across customers).
3. Nested levels: at each level, every key of the node is tried against the
   candidates; the first key (in data order) that leads to a leaf wins.
4. Nothing found -> ``fallbacks[service]`` or ``fallbacks["*"]``. Data is
   never invented.

Serialisation note: bodies are embedded with ``json.dumps(ensure_ascii=False)``
and emitted with ``JSON.stringify``. JavaScript has no int/float distinction,
so a value such as ``9800.0`` in the data is served as ``9800``. That is
numerically equal and parses identically for any JSON client; byte-level
identity of float formatting is not preserved.

Fallbacks format (``fallbacks`` argument / ``--fallbacks`` file)::

    {"*":              {"http": 400, "body": {...}},   # default, required
     "<service name>": {"http": 404, "body": {...}},   # optional, per service
     ...}

``"status"`` is accepted as an alias of ``"http"`` and ``"response"`` as an
alias of ``"body"``. When ``"*"`` is absent a ``404 {"error": "not_found"}``
default is added.

CLI::

    python3 builder/case/mockgen.py <test.json> <out.js> [--fallbacks f.json]
        [--service-path-prefix /voice/v1] [--health-path /health]

Without ``--fallbacks`` the fallbacks are derived from the data
(see ``derive_fallbacks``).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "Layout",
    "DEFAULT_LAYOUT",
    "iter_leaves",
    "build_index",
    "derive_fallbacks",
    "normalize_fallbacks",
    "generate_worker",
    "main",
]


# --------------------------------------------------------------------------
# Data-file layout
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Key names of the test-data *file* (not of the bank's API)."""

    customers_key: str = "customers"
    id_key: str = "customerNo"
    services_key: str = "services"
    status_key: str = "http"
    body_key: str = "response"
    delay_key: str = "delaySeconds"
    description_key: str = "description"

    def is_leaf(self, node: Any) -> bool:
        return (
            isinstance(node, dict)
            and self.status_key in node
            and isinstance(node[self.status_key], int)
            and not isinstance(node[self.status_key], bool)
        )

    def customer_id(self, customer: dict) -> str:
        """Return the customer's id as a string.

        Falls back to the first scalar-valued key when ``id_key`` is absent,
        so a file that names the id differently still works.
        """
        if self.id_key in customer:
            return str(customer[self.id_key]).strip()
        for key, value in customer.items():
            if key != self.services_key and isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value).strip()
        raise ValueError("customer record has no scalar id field")


DEFAULT_LAYOUT = Layout()


# --------------------------------------------------------------------------
# Walking the data
# --------------------------------------------------------------------------


def iter_leaves(test_data: dict, layout: Layout = DEFAULT_LAYOUT) -> Iterator[tuple[str, str, list[str], dict]]:
    """Yield ``(customer_id, service, nested_keys, leaf)`` for every leaf.

    ``nested_keys`` are the keys below the customer level (``[]`` for a
    customer-level service, ``[card]`` or ``[card, week]`` for nested ones),
    always as strings.
    """
    for customer in test_data.get(layout.customers_key, []):
        if not isinstance(customer, dict):
            continue
        cid = layout.customer_id(customer)
        services = customer.get(layout.services_key) or {}
        if not isinstance(services, dict):
            continue
        for service, node in services.items():
            stack: list[tuple[list[str], Any]] = [([], node)]
            while stack:
                chain, cur = stack.pop()
                if layout.is_leaf(cur):
                    yield cid, str(service), chain, cur
                elif isinstance(cur, dict):
                    # reverse so that iteration order follows the file order
                    for key, child in reversed(list(cur.items())):
                        stack.append((chain + [str(key)], child))
                # anything else (list / scalar) is not a valid node: ignored


def build_index(test_data: dict, layout: Layout = DEFAULT_LAYOUT) -> dict[str, dict]:
    """Build the lookup index embedded in the worker.

    ``INDEX[service] = {"depth": int, "byCustomer": {customer_id: node}}``
    where ``node`` is the (leaf or nested) subtree copied from the data with
    all keys stringified and leaves reduced to the status / body / delay keys.
    """
    index: dict[str, dict] = {}
    for cid, service, chain, leaf in iter_leaves(test_data, layout):
        entry = index.setdefault(service, {"depth": len(chain), "byCustomer": {}})
        entry["depth"] = max(entry["depth"], len(chain))
        node = entry["byCustomer"]
        if not chain:
            node[cid] = _leaf_record(leaf, layout)
            continue
        cur = node.setdefault(cid, {})
        for key in chain[:-1]:
            cur = cur.setdefault(key, {})
        cur[chain[-1]] = _leaf_record(leaf, layout)
    return index


def _leaf_record(leaf: dict, layout: Layout) -> dict:
    rec = {"http": int(leaf[layout.status_key]), "response": leaf.get(layout.body_key)}
    delay = leaf.get(layout.delay_key)
    if isinstance(delay, (int, float)) and not isinstance(delay, bool) and delay > 0:
        rec["delaySeconds"] = delay
    return rec


# --------------------------------------------------------------------------
# Fallbacks
# --------------------------------------------------------------------------

_FIXED_GUID = "00000000000000000000000000000000"


def normalize_fallbacks(fallbacks: dict | None) -> dict[str, dict]:
    """Return ``{name: {"http": int, "body": obj}}`` with a guaranteed ``"*"``."""
    out: dict[str, dict] = {}
    for name, spec in (fallbacks or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"fallback for {name!r} must be an object")
        status = spec.get("http", spec.get("status"))
        if not isinstance(status, int) or isinstance(status, bool):
            raise ValueError(f"fallback for {name!r} needs an integer 'http' status")
        body = spec["body"] if "body" in spec else spec.get("response")
        out[str(name)] = {"http": status, "body": body}
    out.setdefault("*", {"http": 404, "body": {"error": "not_found"}})
    return out


def _replace_keys(obj: Any, replacements: dict[str, Any]) -> Any:
    """Deep-copy ``obj`` replacing the value of every key (case-insensitive)
    listed in ``replacements``."""
    if isinstance(obj, dict):
        return {
            k: (replacements[k.lower()] if isinstance(k, str) and k.lower() in replacements else _replace_keys(v, replacements))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_replace_keys(v, replacements) for v in obj]
    return copy.deepcopy(obj)


def derive_fallbacks(test_data: dict, layout: Layout = DEFAULT_LAYOUT) -> dict[str, dict]:
    """Derive not-found fallbacks from the data when none are supplied.

    * per service: the body of any 404 leaf of that service (guid fixed);
    * otherwise a 400 cloned from any error-shaped body (status >= 400) in
      the data with returnCode/code = ``INVALID_ARGUMENTS_ERROR``,
      type = ``ERROR``, messageText/message = ``invalid arguments``;
    * if the data has no error body at all: ``404 {"error": "not_found"}``.

    The builder normally passes explicit fallbacks derived from the service
    contract instead; this is only a data-driven approximation.
    """
    not_found: dict[str, Any] = {}
    any_error: Any = None
    services: list[str] = []
    for _cid, service, _chain, leaf in iter_leaves(test_data, layout):
        if service not in services:
            services.append(service)
        status = leaf[layout.status_key]
        body = leaf.get(layout.body_key)
        if status == 404 and service not in not_found and isinstance(body, (dict, list)):
            not_found[service] = body
        if status >= 400 and any_error is None and isinstance(body, dict):
            any_error = body

    if any_error is not None:
        generic = {
            "http": 400,
            "body": _replace_keys(
                any_error,
                {
                    "guid": _FIXED_GUID,
                    "returncode": "INVALID_ARGUMENTS_ERROR",
                    "code": "INVALID_ARGUMENTS_ERROR",
                    "messagetext": "invalid arguments",
                    "message": "invalid arguments",
                    "type": "ERROR",
                },
            ),
        }
    else:
        generic = {"http": 404, "body": {"error": "not_found"}}

    out: dict[str, dict] = {"*": generic}
    for service in services:
        if service in not_found:
            out[service] = {"http": 404, "body": _replace_keys(not_found[service], {"guid": _FIXED_GUID})}
        else:
            out[service] = copy.deepcopy(generic)
    return out


# --------------------------------------------------------------------------
# Worker generation
# --------------------------------------------------------------------------

_WORKER_TEMPLATE = r"""// GENERATED FILE - do not edit by hand.
// Generated by builder/case/mockgen.py from test data:
//   __DESCRIPTION__
//
// Cloudflare Worker (ES module) that mocks the bank's services exactly:
// every leaf of the test data is served with its status code and body.
// Routing: POST <any prefix>/<service>  (last path segment == service name);
//          the contract path "__PREFIX__/<service>" is accepted as well.
//          GET __HEALTH__ -> {"ok":true,"services":[...],"customers":N}
// Lookup keys are taken from the values of the request's "data" object
// (numbers and strings compare equal when their trimmed string forms match).
// Not found -> the per-service fallback (or "*"). Nothing is ever invented.
// Delays: a leaf with "delaySeconds" waits before answering unless
//         globalThis.MOCK_NO_DELAY or the env binding MOCK_NO_DELAY is set.
// Note: JavaScript numbers have no int/float distinction, so e.g. 9800.0
//       in the data is served as 9800 (numerically identical).

const SERVICE_PATH_PREFIX = __PREFIX_JSON__;
const HEALTH_PATH = __HEALTH_JSON__;
const CUSTOMER_COUNT = __CUSTOMER_COUNT__;

/** INDEX[service] = { depth, byCustomer: { customerId: leafOrNestedNode } } */
const INDEX = __INDEX__;

/** FALLBACKS[service | "*"] = { http, body } */
const FALLBACKS = __FALLBACKS__;

const SERVICES = Object.keys(INDEX);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "*",
};

const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
const isLeaf = (node) => node !== null && typeof node === "object" && !Array.isArray(node) && has(node, "http") && typeof node.http === "number";
const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function json(body, status = 200) {
  return new Response(JSON.stringify(body === undefined ? null : body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...CORS },
  });
}

async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

// Unfilled template variables, e.g. "{{customerNumber}}" (or URL-encoded).
function isTemplatePlaceholder(s) {
  return s.includes("{{") || s.includes("%7B%7B") || s.includes("%7b%7b");
}

/** Comparable string forms of a scalar value. */
function variants(value) {
  if (value === null || value === undefined || typeof value === "object") return [];
  const s = String(value).trim();
  if (!s || isTemplatePlaceholder(s)) return [];
  const out = new Set([s]);
  const compact = s.replace(/\s+/g, "");
  out.add(compact);
  let digits = compact;
  const m = /^(\d+)\.0+$/.exec(compact);
  if (m) digits = m[1];
  if (/^\d+$/.test(digits)) {
    out.add(digits);
    out.add(digits.replace(/^0+(?=\d)/, ""));
  }
  return [...out];
}

/** Candidate lookup values from the request data: scalars, plus scalars one level down. */
function candidates(data) {
  const out = new Set();
  const add = (v) => { for (const s of variants(v)) out.add(s); };
  const addShallow = (obj) => {
    if (Array.isArray(obj)) { for (const v of obj) { if (isPlainObject(v)) addShallow(v); else add(v); } }
    else if (isPlainObject(obj)) { for (const v of Object.values(obj)) add(v); }
  };
  if (!isPlainObject(data)) return out;
  for (const v of Object.values(data)) {
    if (v !== null && typeof v === "object") addShallow(v);
    else add(v);
  }
  return out;
}

function keyMatches(key, cands) {
  for (const s of variants(key)) if (cands.has(s)) return true;
  return false;
}

/** Walk a node down to a leaf, matching one key per level against the candidates. */
function descend(node, cands) {
  if (isLeaf(node)) return node;
  if (!isPlainObject(node)) return null;
  for (const key of Object.keys(node)) {
    if (!keyMatches(key, cands)) continue;
    const found = descend(node[key], cands);
    if (found) return found;
  }
  return null;
}

function lookup(service, data) {
  const entry = INDEX[service];
  if (!entry) return null;
  const cands = candidates(data);
  if (cands.size === 0) return null;
  const byCustomer = entry.byCustomer;
  // 1) a request value names the customer
  let matchedCustomer = false;
  for (const cid of Object.keys(byCustomer)) {
    if (!keyMatches(cid, cands)) continue;
    matchedCustomer = true;
    const found = descend(byCustomer[cid], cands);
    if (found) return found;
  }
  if (matchedCustomer) return null;
  // 2) no customer id in the request: a nested key (e.g. card number) must identify
  //    the record uniquely across customers.
  let found = null;
  for (const cid of Object.keys(byCustomer)) {
    const node = byCustomer[cid];
    if (isLeaf(node)) continue; // customer-level service: needs the customer id
    const hit = descend(node, cands);
    if (!hit) continue;
    if (found) return null; // ambiguous: never guess
    found = hit;
  }
  return found;
}

function delaysDisabled(env) {
  const g = globalThis.MOCK_NO_DELAY;
  const e = env && env.MOCK_NO_DELAY;
  const truthy = (v) => v !== undefined && v !== null && v !== false && String(v) !== "" && String(v) !== "0" && String(v).toLowerCase() !== "false";
  return truthy(g) || truthy(e);
}

function fallbackFor(service) {
  const fb = (service && has(FALLBACKS, service)) ? FALLBACKS[service] : FALLBACKS["*"];
  return json(fb.body, fb.http);
}

function serviceFromPath(pathname) {
  const path = pathname.replace(/\/+$/, "");
  if (SERVICE_PATH_PREFIX) {
    const prefix = SERVICE_PATH_PREFIX.replace(/\/+$/, "");
    if (path.startsWith(prefix + "/")) {
      const rest = decodeURIComponent(path.slice(prefix.length + 1));
      if (has(INDEX, rest)) return rest;
    }
  }
  const last = decodeURIComponent(path.split("/").pop() || "");
  return has(INDEX, last) ? last : null;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (request.method === "GET" && url.pathname.replace(/\/+$/, "") === HEALTH_PATH.replace(/\/+$/, "")) {
      return json({ ok: true, services: SERVICES, customers: CUSTOMER_COUNT });
    }
    if (request.method !== "POST") {
      return json({ error: "not_found" }, 404);
    }
    const service = serviceFromPath(url.pathname);
    if (!service) return fallbackFor(null);
    const body = await readBody(request);
    const data = isPlainObject(body) && isPlainObject(body.data) ? body.data : (isPlainObject(body) ? body : {});
    const leaf = lookup(service, data);
    if (!leaf) return fallbackFor(service);
    if (leaf.delaySeconds && !delaysDisabled(env)) {
      await new Promise((resolve) => setTimeout(resolve, Number(leaf.delaySeconds) * 1000));
    }
    return json(leaf.response, leaf.http);
  },
};
"""


def _js_json(value: Any) -> str:
    """JSON that is also a valid JS expression (ensure_ascii=False keeps the
    bytes readable; U+2028/2029 are legal in ES2019+ string literals but we
    escape them anyway for older tooling)."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _comment_safe(text: str, limit: int = 600) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text or "(no description)"


def generate_worker(
    test_data: dict,
    fallbacks: dict,
    *,
    service_path_prefix: str = "",
    health_path: str = "/health",
    layout: Layout = DEFAULT_LAYOUT,
) -> str:
    """Return the source of the mock Worker (ES module) for ``test_data``.

    ``fallbacks`` is ``{service_name: {"http": int, "body": body}, "*": {...}}``
    (see module docstring). ``service_path_prefix`` is the contract's path
    prefix (e.g. ``"/voice/v1"``); it is accepted in addition to the generic
    last-path-segment routing. ``health_path`` answers GET with a status JSON.
    """
    if not isinstance(test_data, dict):
        raise TypeError("test_data must be the parsed test JSON object")
    index = build_index(test_data, layout)
    if not index:
        raise ValueError("no service leaves found in test data")
    fb = normalize_fallbacks(fallbacks)
    customers = test_data.get(layout.customers_key) or []
    description = test_data.get(layout.description_key) or ""
    prefix = service_path_prefix.rstrip("/")
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix
    health = health_path if health_path.startswith("/") else "/" + health_path
    return (
        _WORKER_TEMPLATE.replace("__DESCRIPTION__", _comment_safe(description))
        .replace("__PREFIX_JSON__", _js_json(prefix))
        .replace("__HEALTH_JSON__", _js_json(health))
        .replace("__PREFIX__", prefix or "")
        .replace("__HEALTH__", health)
        .replace("__CUSTOMER_COUNT__", str(len(customers)))
        .replace("__INDEX__", _js_json(index))
        .replace("__FALLBACKS__", _js_json(fb))
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Cloudflare Worker mock from a test-data JSON file.")
    parser.add_argument("test_json", help="test data (customers -> services -> {http, response})")
    parser.add_argument("out_js", help="path of the worker module to write")
    parser.add_argument("--fallbacks", help="JSON file {service|'*': {http, body}}; derived from the data when omitted")
    parser.add_argument("--service-path-prefix", default="", help="contract path prefix, e.g. /voice/v1")
    parser.add_argument("--health-path", default="/health")
    parser.add_argument("--print-fallbacks", action="store_true", help="print the fallbacks used (JSON) to stderr")
    args = parser.parse_args(argv)

    test_data = _load_json(args.test_json)
    if args.fallbacks:
        fallbacks = normalize_fallbacks(_load_json(args.fallbacks))
    else:
        fallbacks = derive_fallbacks(test_data)
    if args.print_fallbacks:
        print(json.dumps(fallbacks, ensure_ascii=False, indent=2), file=sys.stderr)

    source = generate_worker(
        test_data,
        fallbacks,
        service_path_prefix=args.service_path_prefix,
        health_path=args.health_path,
    )
    out = Path(args.out_js)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source, encoding="utf-8")
    leaves = sum(1 for _ in iter_leaves(test_data))
    services = sorted(build_index(test_data))
    print(f"wrote {out} ({len(source)} bytes): {leaves} leaves, {len(services)} services: {', '.join(services)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
