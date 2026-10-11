#!/usr/bin/env python3
"""Final-case pipeline: case folder + mock URL -> mock worker, case policy, Freya workflow, export, trace.

    python3 builder/build.py <case_folder> <mock_url> [--slot final] [--no-deploy-mock] [--skip-save] [--live-test]

Generic: every bank value (service names, fields, wording, limits) is read from the case folder at run time.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "builder" / "case"))

from builder.lint import dry_run, lint_workflow  # noqa: E402
from builder.trace import Trace  # noqa: E402
from ingest_case import ingest_case  # noqa: E402
from contract import parse_contract_markdown, parse_openapi_file, mock_fallbacks  # noqa: E402
from extract_case import extract_case_policy, validate_policy  # noqa: E402
from compile_case import compile_case  # noqa: E402
from mockgen import generate_worker  # noqa: E402

BUDGET_S = 30 * 60


def _run(cmd, cwd=None, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def build_case(case_folder: str, mock_url: Optional[str], *, slot: str = "final", out_dir: Optional[Path] = None,
               deploy_mock: bool = True, skip_save: bool = False, export_path: Optional[Path] = None,
               live_test: bool = False, trace_copy: Optional[Path] = None, policy_path_in: Optional[Path] = None) -> Dict[str, Any]:
    t0 = time.time()
    out_dir = out_dir or (ROOT / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(ROOT / "traces")
    result: Dict[str, Any] = {"slot": slot, "case_folder": os.path.abspath(case_folder), "mock_url": mock_url}
    trace.log({"event": "case", "folder": result["case_folder"], "mock_url": mock_url})

    # 1. ingest
    case = ingest_case(case_folder)
    trace.log({"event": "ingest", "files": case["all_files"], "brief": (case.get("brief") or {}).get("path"),
               "contract": (case.get("contract") or {}).get("path"), "test_data": case.get("test_data_path")})
    if not case.get("contract"):
        openapi = next((os.path.join(case["folder"], f) for f in case["all_files"] if f.endswith("openapi.json")), None)
        if not openapi:
            raise SystemExit("no service contract found in case folder")
        services_ir = parse_openapi_file(openapi)
    else:
        services_ir = parse_contract_markdown(case["contract"]["text"])
    json.dump(services_ir, open(out_dir / f"{slot}.services.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    trace.log({"event": "contract", "services": services_ir["names"], "base_url": services_ir.get("base_url")})

    # 2. mock worker from test data
    mock_dir = ROOT / "mock"
    if case.get("test_data"):
        fallbacks = mock_fallbacks(services_ir)
        prefix = ""
        base = services_ir.get("base_url") or ""
        import re as _re
        m = _re.match(r"https?://[^/]+(/.*)?$", base)
        if m and m.group(1):
            prefix = m.group(1).rstrip("/")
        worker = generate_worker(case["test_data"], fallbacks, service_path_prefix=prefix)
        (mock_dir / "src").mkdir(parents=True, exist_ok=True)
        (mock_dir / "src" / "index.js").write_text(worker, encoding="utf-8")
        trace.log({"event": "mock_generated", "path": "mock/src/index.js", "bytes": len(worker), "services": services_ir["names"]})
        # offline exact-match test of the worker
        req_fields = {n: services_ir["services"][n]["request_fields"] for n in services_ir["names"]}
        rf_path = out_dir / f"{slot}.request-fields.json"
        json.dump(req_fields, open(rf_path, "w"), indent=1)
        tm = _run([sys.executable, str(ROOT / "builder/case/test_mock.py"), case["test_data_path"], str(mock_dir / "src/index.js"), "--request-fields", str(rf_path)], timeout=600)
        summary = (tm.stdout.strip().splitlines() or [""])[-1]
        trace.test("mock_exact_match", passed=(tm.returncode == 0), summary=summary)
        result["mock_tests"] = summary
        print("mock tests:", summary, flush=True)
        if deploy_mock and mock_url:
            try:
                dp = _run(["npx", "--yes", "wrangler", "deploy"], cwd=str(mock_dir), timeout=300)
                ok = dp.returncode == 0
                trace.freya("mock_deploy", ok=ok, tail=(dp.stdout + dp.stderr)[-600:])
                result["mock_deployed"] = ok
                print("mock deploy:", "ok" if ok else "FAILED", flush=True)
            except Exception as e:  # deploy is best effort; a running worker may already serve the same data
                trace.freya("mock_deploy", ok=False, error=str(e))
        if mock_url:
            try:
                import urllib.request
                req = urllib.request.Request(mock_url.rstrip("/") + "/health", headers={"User-Agent": "Mozilla/5.0 (builder health check)"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    health = json.loads(r.read().decode("utf-8"))
                trace.test("mock_health", passed=bool(health.get("ok")), health=health)
                result["mock_health"] = health
            except Exception as e:
                trace.test("mock_health", passed=False, error=str(e))

    # 3. policy extraction (model call, logged)
    if policy_path_in:
        policy = json.load(open(policy_path_in, encoding="utf-8"))
        trace.log({"event": "policy_reused", "path": str(policy_path_in), "note": "extraction skipped; policy from an earlier run of this builder on the same folder"})
    else:
        policy = extract_case_policy(case, services_ir, trace=trace)
    policy_path = out_dir / f"{slot}.policy.json"
    json.dump(policy, open(policy_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    errs = validate_policy(policy) if "phrases" in policy else ["extraction returned no policy"]
    trace.log({"event": "policy", "path": str(policy_path), "valid": not errs, "errors": errs})
    if errs:
        raise SystemExit("case policy invalid: " + "; ".join(errs))
    result["policy_path"] = str(policy_path)

    # 4. compile + lint
    workflow = compile_case(policy, services_ir, mock_url)
    wf_path = out_dir / f"{slot}.workflow.json"
    json.dump(workflow, open(wf_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lint_ok, lint_bad = lint_workflow(workflow)
    trace.log({"event": "compiled", "path": str(wf_path), "nodes": len(workflow["nodes"]), "lint_ok": lint_ok, "lint": lint_bad})
    result["workflow_path"] = str(wf_path)
    if not lint_ok:
        raise SystemExit("lint failed: " + json.dumps(lint_bad))

    # 5. offline behaviour tests on the compiled code nodes
    env = dict(os.environ)
    if case.get("test_data_path"):
        env["CASE_TEST_DATA"] = case["test_data_path"]
    tc = subprocess.run([sys.executable, str(ROOT / "builder/case/test_case.py"), str(wf_path), str(policy_path)], capture_output=True, text=True, timeout=600, env=env)
    tc_summary = (tc.stdout.strip().splitlines() or [""])[-1]
    trace.test("offline_case_tests", passed=(tc.returncode == 0), summary=tc_summary)
    result["offline_tests"] = tc_summary
    print("offline tests:", tc_summary, flush=True)
    if tc.returncode != 0:
        print(tc.stdout[-3000:], tc.stderr[-2000:])
        raise SystemExit("offline case tests failed")

    # 6. save to Freya + export
    if os.environ.get("FREYA_API_KEY") and not skip_save:
        import freya  # noqa: E402
        try:
            dr = dry_run(workflow, f"dry-{slot}")
            trace.freya("validate_workflow", ok=True, detail=str(dr)[:400])
        except Exception as e:
            trace.freya("validate_workflow", ok=False, error=str(e)[:400])
        old_cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            saved = freya.save_workflow(slot, workflow)
        finally:
            os.chdir(old_cwd)
        trace.freya("save_workflow", slot=slot, **saved)
        result.update(saved)
        try:
            from builder.harden import apply_hardening
            h = apply_hardening(saved["workflow_id"], {"never_say": []}, temperature=0.2)
            trace.freya("harden", **h)
        except Exception as e:
            trace.freya("harden", ok=False, error=str(e)[:300])
        export_path = export_path or (ROOT / "submission" / f"{slot}.workflow.json")
        export_path.parent.mkdir(parents=True, exist_ok=True)
        freya.export_workflow(saved["workflow_id"], str(export_path))
        trace.freya("export_workflow", path=str(export_path))
        result["export_path"] = str(export_path)
        if live_test and time.time() - t0 < BUDGET_S - 600:
            lt = _run([sys.executable, str(ROOT / "tools/run_final_cases.py"), saved["agent_id"], "--out", str(ROOT / "results" / f"{slot}-live.json")], timeout=max(60, int(BUDGET_S - (time.time() - t0) - 60)))
            lines = [l for l in lt.stdout.splitlines() if l.startswith(("PASS", "FAIL"))]
            trace.test("live_simulation", passed=all(l.startswith("PASS") for l in lines) if lines else False, rows=lines[:40], returncode=lt.returncode)
            result["live_test"] = lines
            print("\n".join(lines), flush=True)
    else:
        trace.log({"event": "skip_save", "reason": "no FREYA_API_KEY" if not os.environ.get("FREYA_API_KEY") else "--skip-save"})

    result["seconds"] = round(time.time() - t0, 1)
    path = trace.close(**{k: result.get(k) for k in ("workflow_id", "agent_id", "seconds")})
    result["trace"] = str(path)
    if trace_copy:
        trace_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, trace_copy)
        result["trace_copy"] = str(trace_copy)
    return result
