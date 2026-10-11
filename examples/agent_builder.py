"""Run the guarded builder on one or more briefs.

    python3 examples/agent_builder.py briefs/brief-a.md
    python3 examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md

This calls builder/build.py. If a practice run fails, change the policy JSON and run the builder again.
Do not hand-edit the workflow JSON.
"""
import json, pathlib, subprocess, sys, time

KIT = pathlib.Path(__file__).resolve().parent.parent


def main(briefs):
    names = [pathlib.Path(b).name for b in briefs]
    if any("brief-b" in name for name in names) and len(briefs) > 1:
        slot = "brief-b"
    else:
        slot = pathlib.Path(briefs[-1]).stem
    trace = KIT / "traces" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    trace.parent.mkdir(exist_ok=True)
    cmd = [sys.executable, str(KIT / "builder" / "build.py"), *briefs, "--slot", slot]
    with trace.open("w") as out:
        out.write(json.dumps({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "briefs": briefs, "cmd": cmd}) + "\n")
        proc = subprocess.run(cmd, cwd=KIT)
        out.write(json.dumps({"end": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "code": proc.returncode, "trace": str(trace)}) + "\n")
    print(f"builder exit {proc.returncode}. Trace: {trace}")
    return proc.returncode


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
