#!/usr/bin/env python3
"""Beast-mode preflight for bank-grade Freya collections agents.

τ-bench mapping
  - db_check / final state  → ledger `expected` (PUB live + SYN offline)
  - communicate_checks      → case `checks` (judge on live runs)
  - pass^k                  → `run_cases.py --reps k` (finals: k=3)

What this script runs (offline, no Freya):
  1. builder/test_guards.py
  2. builder/test_synthetic_scenarios.py  (40 SYN cases, offline subset)
  3. refreshes synthetic-test-set.json + out/redteam-cases.json

Live Freya (optional, needs agent_id + env):
  tools/run_cases.py <agent_id> --reps 3
  tools/run_cases.py <agent_id> --style talkative|pieces|corrects|asks_back|in_words

Note: the hosted practice bank only accepts PUB-01…PUB-13. SYN-* cannot open
sessions; their monetary fail-closed paths are covered offline.

    python3 tools/beast_mode.py
    python3 tools/beast_mode.py --live <agent_id> --reps 2
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> int:
    print("\n$", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", metavar="AGENT_ID", help="also run live PUB cases")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--brief", choices=["A", "B"])
    ap.add_argument("--style")
    ap.add_argument("--skip-offline", action="store_true")
    a = ap.parse_args()

    codes = []
    if not a.skip_offline:
        codes.append(run([sys.executable, "builder/test_guards.py"]))
        codes.append(run([sys.executable, "builder/test_synthetic_scenarios.py", "--write"]))
        codes.append(run([sys.executable, "builder/redteam.py"]))

    syn = json.loads((ROOT / "synthetic-test-set.json").read_text())
    cov = syn["meta"]["coverage"]
    print("\n=== Coverage summary ===")
    print(f"public PUB cases: {cov['public_cases']}")
    print(f"synthetic SYN cases: {cov['synthetic_cases']}")
    print(f"critical monetary SYN: {len(cov['critical_monetary'])}")
    print("Brief rules thin in public set:")
    for line in cov["brief_a_rules_thin_in_public"]:
        print(" ", "-", line)

    if a.live:
        cmd = [
            sys.executable,
            "-u",
            "tools/run_cases.py",
            a.live,
            "--reps",
            str(a.reps),
            "--out",
            f"results/beast-live-r{a.reps}.json",
        ]
        if a.brief:
            cmd.extend(["--brief", a.brief])
        if a.style:
            cmd.extend(["--style", a.style])
        codes.append(run(cmd))
    else:
        print(
            "\nLive pass^k (τ-bench style) — run when ready:\n"
            "  FREYA_SIM_CONCURRENCY=5 python3 -u tools/run_cases.py <agent_id> "
            "--reps 3 --out results/beast-pass3.json\n"
            "  python3 -u tools/run_cases.py <agent_id> --style talkative --reps 2\n"
            "  python3 -u tools/run_cases.py <agent_id> --style in_words --reps 2"
        )

    bad = [c for c in codes if c]
    print(f"\nbeast_mode exit: {'FAIL' if bad else 'PASS'} (steps={len(codes)}, failed={len(bad)})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
