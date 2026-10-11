#!/usr/bin/env python3
"""Build a Freya phone agent from brief files or a final-case folder.

Practice:
    python3 builder/build.py briefs/brief-a.md briefs/brief-b.md --slot brief-a-b

Final (submission contract):
    python3 builder/build.py FreyaBank-Ekstre-Borc/ https://your-mock.workers.dev
    # writes submission/final.workflow.json and traces/final.log
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load .env
for env_file in (ROOT / ".env", ROOT / "env.sh"):
    if not env_file.exists():
        continue
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        k, v = line.split("=", 1)
        v = v.split("#", 1)[0]
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from builder.compile import compile_workflow  # noqa: E402
from builder.extract import extract_policy, save_policy, validate_policy  # noqa: E402
from builder.lint import dry_run, lint_workflow  # noqa: E402
from builder.redteam import write_cases  # noqa: E402
from builder.trace import Trace  # noqa: E402


DOC_GLOBS = ("*.md", "*.txt", "*.markdown")
OPENAPI_NAMES = (
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "tools/openapi.json",
    "service/openapi.json",
    "contract/openapi.json",
)


def _looks_like_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def discover_case(folder: Path) -> Tuple[List[str], Path]:
    """Find brief-like docs and an openapi file inside a final-case folder."""
    folder = folder.resolve()
    if not folder.is_dir():
        raise SystemExit(f"case folder not found: {folder}")

    openapi: Optional[Path] = None
    for name in OPENAPI_NAMES:
        cand = folder / name
        if cand.is_file():
            openapi = cand
            break
    if openapi is None:
        for cand in sorted(folder.rglob("openapi.json")):
            openapi = cand
            break
    if openapi is None:
        openapi = ROOT / "tools" / "openapi.json"
        print(f"warning: no openapi in {folder}; falling back to {openapi}")

    def _file_key(p: Path) -> str:
        # macOS case-insensitive FS: resolve() keeps the requested casing
        try:
            return str(p.stat().st_ino)
        except OSError:
            return str(p.resolve()).lower()

    preferred = []
    seen: set[str] = set()
    for name in ("BRIEF.md", "README.md"):
        p = folder / name
        if p.is_file():
            key = _file_key(p)
            if key not in seen:
                seen.add(key)
                preferred.append(str(p.resolve()))

    docs: List[str] = list(preferred)
    for pattern in DOC_GLOBS:
        for p in sorted(folder.rglob(pattern)):
            if p.name.startswith("."):
                continue
            key = _file_key(p)
            if key in seen:
                continue
            seen.add(key)
            docs.append(str(p.resolve()))

    # Prefer documents over huge dumps: cap to first 40 text files
    docs = docs[:40]
    if not docs:
        raise SystemExit(f"no brief documents found under {folder}")
    return docs, openapi


def build(
    briefs: list[str],
    *,
    slot: str | None = None,
    out_dir: Path | None = None,
    openapi_path: Path | None = None,
    mock_url: str | None = None,
    run_test: bool = False,
    run_redteam: bool = False,
    skip_save: bool = False,
    repair_rounds: int = 0,
    export_submission: bool = False,
    trace_final: bool = False,
) -> dict:
    out_dir = out_dir or (ROOT / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_dir = ROOT / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(traces_dir)
    slot = slot or Path(briefs[0]).stem
    openapi_path = Path(openapi_path or ROOT / "tools" / "openapi.json")

    trace.log(
        {
            "event": "inputs",
            "briefs": briefs,
            "openapi": str(openapi_path),
            "mock_url": mock_url,
            "slot": slot,
        }
    )

    policy = extract_policy(briefs, openapi_path, base_url=mock_url)
    errs = validate_policy(policy)
    if errs:
        raise SystemExit("policy invalid: " + "; ".join(errs))
    policy_path = out_dir / f"{slot}.policy.json"
    save_policy(policy, policy_path)
    trace.log({"event": "policy", "path": str(policy_path)})

    workflow = compile_workflow(policy, openapi_path, base_url=mock_url)
    wf_path = out_dir / f"{slot}.workflow.json"
    wf_path.write_text(json.dumps(workflow, indent=1))
    trace.log({"event": "compiled", "path": str(wf_path)})

    nodes = workflow.get("nodes") or {}
    if isinstance(nodes, dict):
        n_steps = len(nodes)
        n_tools = sum(len(n.get("tools") or []) for n in nodes.values() if isinstance(n, dict))
    else:
        n_steps = len(nodes)
        n_tools = sum(len(n.get("tools") or []) for n in nodes if isinstance(n, dict))
    top_tools = len(workflow.get("tools") or [])
    trace.log(
        {
            "event": "graph_stats",
            "steps": n_steps,
            "node_tool_slots": n_tools,
            "top_level_tools": top_tools,
        }
    )

    ok, lint_errs = lint_workflow(workflow, openapi_path)
    trace.log({"event": "lint", "ok": ok, "errors": lint_errs})
    if not ok:
        print("LINT FAIL:")
        for e in lint_errs:
            print("-", e)
        raise SystemExit(1)
    print("lint ok")

    result = {
        "policy_path": str(policy_path),
        "workflow_path": str(wf_path),
        "slot": slot,
        "mock_url": mock_url,
    }
    if os.environ.get("FREYA_API_KEY") and not skip_save:
        try:
            dry = dry_run(workflow, f"dry-{slot}")
            trace.freya("dryRun", result=str(dry)[:300])
            print("dry-run ok")
        except Exception as e:
            trace.freya("dryRun", error=str(e)[:500])
            print("dry-run warning:", e)

        import freya
        from builder.harden import apply_hardening

        saved = freya.save_workflow(slot, workflow)
        trace.freya("save_workflow", **saved)
        result.update(saved)
        print(json.dumps(saved))
        try:
            hard = apply_hardening(saved["workflow_id"], policy, temperature=0.2)
            trace.freya("harden", **hard)
            result["harden"] = hard
            print("hardened:", json.dumps(hard))
        except Exception as e:
            trace.freya("harden", error=str(e)[:400])
            print("harden warning:", e)

        if export_submission:
            sub_dir = ROOT / "submission"
            sub_dir.mkdir(parents=True, exist_ok=True)
            export_path = sub_dir / "final.workflow.json"
            freya.export_workflow(saved["workflow_id"], str(export_path))
            trace.freya("export_workflow", path=str(export_path))
            result["submission_workflow"] = str(export_path)
            print("exported:", export_path)

        if run_redteam:
            cases_path = write_cases(policy, out_dir / f"{slot}-redteam.json")
            trace.test("redteam_cases", path=str(cases_path))

        if run_test:
            agent_id = saved["agent_id"]
            brief_letter = "A"
            if any("brief-b" in Path(b).name.lower() for b in briefs):
                brief_letter = "A" if slot.endswith("a") or "brief-a" in slot else "B"
            cmd = f"{sys.executable} tools/run_cases.py {agent_id} --brief {brief_letter}"
            trace.test("run_cases", cmd=cmd)
            print("running", cmd)
            rc = os.system(cmd)
            result["test_exit"] = rc
            for i in range(repair_rounds):
                if rc == 0:
                    break
                hint = out_dir / f"{slot}.repair-hint.txt"
                hint.write_text(
                    f"Repair round {i+1}: edit {policy_path} only, then re-run builder/build.py. "
                    "Do not hand-edit the workflow JSON.\n"
                )
                trace.log({"event": "repair_hint", "round": i + 1, "path": str(hint)})
                print(hint.read_text())
                break
    else:
        print("FREYA_API_KEY not set or --skip-save: wrote files only")
        if export_submission:
            sub_dir = ROOT / "submission"
            sub_dir.mkdir(parents=True, exist_ok=True)
            export_path = sub_dir / "final.workflow.json"
            export_path.write_text(
                json.dumps(
                    {
                        "workflow_id": None,
                        "agent_id": None,
                        "note": "offline export — re-run with FREYA_API_KEY to export from platform",
                        "workflow": workflow,
                    },
                    indent=1,
                )
            )
            result["submission_workflow"] = str(export_path)
            print("wrote offline submission draft:", export_path)

    write_cases(policy, out_dir / f"{slot}-redteam.json")

    path = trace.close(**{k: result.get(k) for k in ("workflow_id", "agent_id")})
    result["trace"] = str(path)
    if trace_final or export_submission:
        final_log = traces_dir / "final.log"
        # Human-readable copy for submission.json "trace"
        lines = [
            f"builder trace start={trace.start}",
            f"source_jsonl={path}",
            f"slot={slot}",
            f"mock_url={mock_url or ''}",
            f"workflow_id={result.get('workflow_id')}",
            f"agent_id={result.get('agent_id')}",
            f"workflow_path={result.get('workflow_path')}",
            f"submission={result.get('submission_workflow')}",
            "",
            "--- jsonl ---",
            path.read_text(),
        ]
        final_log.write_text("\n".join(lines))
        # also keep a copy of the jsonl next to it
        shutil.copy2(path, traces_dir / "final.jsonl")
        result["trace_final"] = str(final_log)
        print("trace:", final_log)
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # Final submission shape: build.py {case_folder} {mock_url}
    if len(argv) >= 2 and not argv[0].startswith("-") and _looks_like_url(argv[1]):
        case_folder = Path(argv[0])
        mock_url = argv[1].rstrip("/")
        rest = argv[2:]
        briefs, openapi = discover_case(case_folder)
        ap = argparse.ArgumentParser(description=__doc__)
        ap.add_argument("--slot", default="final")
        ap.add_argument("--out", type=Path, default=ROOT / "out")
        ap.add_argument("--skip-save", action="store_true")
        ap.add_argument("--test", action="store_true")
        args = ap.parse_args(rest)
        t0 = time.time()
        print(f"final mode: case={case_folder} mock={mock_url}")
        print(f"docs ({len(briefs)}):", ", ".join(Path(b).name for b in briefs[:12]), "...")
        print("openapi:", openapi)
        result = build(
            briefs,
            slot=args.slot,
            out_dir=args.out,
            openapi_path=openapi,
            mock_url=mock_url,
            run_test=args.test,
            skip_save=args.skip_save,
            export_submission=True,
            trace_final=True,
        )
        print(f"done in {time.time() - t0:.1f}s")
        print(
            json.dumps(
                {
                    k: result.get(k)
                    for k in (
                        "workflow_id",
                        "agent_id",
                        "workflow_path",
                        "submission_workflow",
                        "trace_final",
                        "mock_url",
                    )
                },
                indent=2,
            )
        )
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("briefs", nargs="+", help="Brief markdown files")
    ap.add_argument("--slot", help="Freya save_workflow slot (default: first brief stem)")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--openapi", type=Path, default=None)
    ap.add_argument("--mock-url", default=None, help="Override tool base URL (Workers mock)")
    ap.add_argument("--test", action="store_true", help="Run practice cases after save")
    ap.add_argument("--redteam", action="store_true", help="Generate+note redteam cases")
    ap.add_argument("--skip-save", action="store_true")
    ap.add_argument("--repair-rounds", type=int, default=0)
    ap.add_argument("--export-submission", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.time()
    result = build(
        args.briefs,
        slot=args.slot,
        out_dir=args.out,
        openapi_path=args.openapi,
        mock_url=args.mock_url,
        run_test=args.test,
        run_redteam=args.redteam,
        skip_save=args.skip_save,
        repair_rounds=args.repair_rounds,
        export_submission=args.export_submission,
        trace_final=args.export_submission,
    )
    print(f"done in {time.time() - t0:.1f}s")
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("workflow_id", "agent_id", "workflow_path", "trace", "submission_workflow")
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
