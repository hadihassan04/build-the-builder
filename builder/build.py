#!/usr/bin/env python3
"""Build a Freya phone agent from brief files.

    python3 builder/build.py briefs/brief-a.md briefs/brief-b.md
    python3 builder/build.py briefs/brief-a.md --slot brief-a --test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

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


def build(
    briefs: list[str],
    *,
    slot: str | None = None,
    out_dir: Path | None = None,
    run_test: bool = False,
    run_redteam: bool = False,
    skip_save: bool = False,
    repair_rounds: int = 0,
) -> dict:
    out_dir = out_dir or (ROOT / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(ROOT / "traces")
    slot = slot or Path(briefs[0]).stem

    policy = extract_policy(briefs, ROOT / "tools/openapi.json")
    errs = validate_policy(policy)
    if errs:
        raise SystemExit("policy invalid: " + "; ".join(errs))
    policy_path = out_dir / f"{slot}.policy.json"
    save_policy(policy, policy_path)
    trace.log({"event": "policy", "path": str(policy_path)})

    workflow = compile_workflow(policy, ROOT / "tools/openapi.json")
    wf_path = out_dir / f"{slot}.workflow.json"
    wf_path.write_text(json.dumps(workflow, indent=1))
    trace.log({"event": "compiled", "path": str(wf_path)})

    ok, lint_errs = lint_workflow(workflow, ROOT / "tools/openapi.json")
    trace.log({"event": "lint", "ok": ok, "errors": lint_errs})
    if not ok:
        print("LINT FAIL:")
        for e in lint_errs:
            print("-", e)
        raise SystemExit(1)
    print("lint ok")

    # dry run if API key present
    result = {"policy_path": str(policy_path), "workflow_path": str(wf_path), "slot": slot}
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

        if run_redteam:
            cases_path = write_cases(policy, out_dir / f"{slot}-redteam.json")
            trace.test("redteam_cases", path=str(cases_path))

        if run_test:
            agent_id = saved["agent_id"]
            # practice set for brief letter
            brief_letter = "B" if any("brief-b" in Path(b).name.lower() for b in briefs) and len(briefs) > 1 else "A"
            if any("brief-b" in Path(b).name.lower() for b in briefs):
                # when both passed, still can run A; user can pass --brief
                brief_letter = "A" if slot.endswith("a") or "brief-a" in slot else "B"
            cmd = f"{sys.executable} tools/run_cases.py {agent_id} --brief {brief_letter}"
            trace.test("run_cases", cmd=cmd)
            print("running", cmd)
            rc = os.system(cmd)
            result["test_exit"] = rc
            # repair loop: only edit policy (manual hook for Claude)
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
                break  # actual LLM repair is done by the skill / agent_builder
    else:
        print("FREYA_API_KEY not set or --skip-save: wrote files only")

    # always write redteam cases offline
    write_cases(policy, out_dir / f"{slot}-redteam.json")

    path = trace.close(**{k: result.get(k) for k in ("workflow_id", "agent_id")})
    result["trace"] = str(path)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("briefs", nargs="+", help="Brief markdown files")
    ap.add_argument("--slot", help="Freya save_workflow slot (default: first brief stem)")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--test", action="store_true", help="Run practice cases after save")
    ap.add_argument("--redteam", action="store_true", help="Generate+note redteam cases")
    ap.add_argument("--skip-save", action="store_true")
    ap.add_argument("--repair-rounds", type=int, default=0)
    args = ap.parse_args(argv)
    t0 = time.time()
    result = build(
        args.briefs,
        slot=args.slot,
        out_dir=args.out,
        run_test=args.test,
        run_redteam=args.redteam,
        skip_save=args.skip_save,
        repair_rounds=args.repair_rounds,
    )
    print(f"done in {time.time() - t0:.1f}s")
    print(json.dumps({k: result.get(k) for k in ("workflow_id", "agent_id", "workflow_path", "trace")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
