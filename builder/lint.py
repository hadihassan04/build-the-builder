"""Static linter for compiled Freya workflows."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from builder.tools_parse import parse_openapi


PRIVILEGED_TARGETS = {
    "identity",
    "load_account",
    "collection",
    "promise_recorded",
    "resolve_and_goodbye",
    "compute_dates",
}


def _walk_nulls(obj: Any, path: str, bad: List[str]) -> None:
    if obj is None:
        bad.append(f"null at {path}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk_nulls(v, f"{path}.{k}", bad)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk_nulls(v, f"{path}[{i}]", bad)


def _find_const(obj: Any, path: str, bad: List[str]) -> None:
    if isinstance(obj, dict):
        if "const" in obj:
            bad.append(f"const at {path} (use default, never const)")
        for k, v in obj.items():
            _find_const(v, f"{path}.{k}", bad)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _find_const(v, f"{path}[{i}]", bad)


def _reachable(nodes: Dict[str, Any], start: str) -> Set[str]:
    seen: Set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in seen or n not in nodes:
            continue
        seen.add(n)
        node = nodes[n]
        for t in node.get("transitions") or []:
            tgt = t.get("target_node")
            if tgt and tgt != "__previous__":
                stack.append(tgt)
        if node.get("passthrough_default_transition"):
            stack.append(node["passthrough_default_transition"])
        if node.get("max_turn_transition"):
            stack.append(node["max_turn_transition"])
    return seen


def lint_workflow(workflow: Dict[str, Any], openapi_path: str | Path | None = None) -> Tuple[bool, List[str]]:
    bad: List[str] = []
    if "agent_config" in workflow:
        bad.append("agent_config must be omitted from submitted workflow")
    _walk_nulls(workflow, "workflow", bad)
    _find_const(workflow, "workflow", bad)

    nodes = workflow.get("nodes") or {}
    initial = workflow.get("initial_node")
    if not initial or initial not in nodes:
        bad.append(f"initial_node missing or unknown: {initial}")

    end_nodes = {k for k, n in nodes.items() if n.get("node_type") == "end_call"}
    if not end_nodes:
        bad.append("no end_call nodes")

    if initial:
        reach = _reachable(nodes, initial)
        for k in nodes:
            if k not in reach:
                bad.append(f"unreachable node: {k}")
        # every reachable conversation/code path should be able to reach an end
        for k in reach:
            n = nodes[k]
            if n.get("node_type") == "end_call":
                continue
            # BFS from k for end
            seen = set()
            stack = [k]
            hit = False
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                if cur in end_nodes:
                    hit = True
                    break
                nn = nodes.get(cur) or {}
                for t in nn.get("transitions") or []:
                    tgt = t.get("target_node")
                    if tgt and tgt != "__previous__":
                        stack.append(tgt)
                if nn.get("passthrough_default_transition"):
                    stack.append(nn["passthrough_default_transition"])
                if nn.get("max_turn_transition"):
                    stack.append(nn["max_turn_transition"])
            if not hit:
                bad.append(f"no path to end_call from {k}")

    for k, n in nodes.items():
        if n.get("passthrough"):
            trans = n.get("transitions") or []
            if not any((t.get("condition") or {}).get("operator") == "always" for t in trans):
                bad.append(f"passthrough node {k} missing always transition")
        if n.get("node_type") == "conversation":
            if n.get("pre_actions"):
                bad.append(f"conversation node {k} has pre_actions (stripped by platform)")
            if n.get("max_node_turns") and not n.get("max_turn_transition"):
                bad.append(f"node {k} has max_node_turns without max_turn_transition")
            for t in n.get("transitions") or []:
                cond = t.get("condition") or {}
                if cond.get("operator") == "matches_intent" and t.get("target_node") in PRIVILEGED_TARGETS:
                    bad.append(f"privileged transition on matches_intent: {k} -> {t.get('target_node')}")
                # nested
                for c in cond.get("conditions") or []:
                    if isinstance(c, dict) and c.get("operator") == "matches_intent" and t.get("target_node") in PRIVILEGED_TARGETS:
                        bad.append(f"privileged matches_intent in group: {k} -> {t.get('target_node')}")

        # write tools need gates
        for tool in n.get("tools") or []:
            fn = (tool.get("function") or {}).get("name")
            if fn in ("create_promise", "schedule_callback", "transfer_to_agent", "add_note"):
                if not tool.get("pre_process_code"):
                    bad.append(f"write tool {fn} on {k} missing pre_process_code gate")

    for tool in workflow.get("tools") or []:
        fn = (tool.get("function") or {}).get("name")
        if fn == "close_call" and not tool.get("pre_process_code"):
            bad.append("lifecycle close_call missing pre_process_code")

    if openapi_path:
        tools_ir = parse_openapi(openapi_path)
        # check enums on transfer/close if present
        for k, n in nodes.items():
            for tool in n.get("tools") or []:
                fn = (tool.get("function") or {}).get("name")
                body = (
                    ((tool.get("function") or {}).get("parameters") or {})
                    .get("properties", {})
                    .get("body", {})
                    .get("properties", {})
                )
                ir_fields = ((tools_ir.get("by_name") or {}).get(fn) or {}).get("fields") or {}
                for fname, fspec in body.items():
                    enum = fspec.get("enum")
                    ir_enum = (ir_fields.get(fname) or {}).get("enum")
                    if enum and ir_enum and set(enum) != set(ir_enum):
                        bad.append(f"enum mismatch {fn}.{fname}: {enum} vs openapi {ir_enum}")

    return (not bad), bad


def lint_file(path: str | Path, openapi_path: str | Path | None = None) -> Tuple[bool, List[str]]:
    wf = json.loads(Path(path).read_text())
    if "workflow" in wf and "nodes" not in wf:
        wf = wf["workflow"]
    return lint_workflow(wf, openapi_path)


def dry_run(workflow: Dict[str, Any], name: str = "lint-dry") -> Any:
    import freya

    return freya.validate_workflow(name, workflow)


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    path = sys.argv[1] if len(sys.argv) > 1 else str(root / "out/brief-a.workflow.json")
    ok, errs = lint_file(path, root / "tools/openapi.json")
    print("PASS" if ok else "FAIL")
    for e in errs:
        print("-", e)
    sys.exit(0 if ok else 1)
