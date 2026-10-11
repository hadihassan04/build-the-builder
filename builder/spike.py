"""Platform spike for the guarded builder.

Probes the Freya API and writes builder/spike_results.json.

    python3 builder/spike.py

Tests:
- pre_hook defanging a write
- post_hook rewriting a tool result
- code-node re-entry mid-call
- close_call pre_hook reading earlier variables
- lifecycle close_call at end_of_call
- whether agent_config.guardrailsConfig and maxPerNTurns are stored
- whether llm temperature survives a simulation model override
"""
import json, os, pathlib, sys, time, traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env(path):
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_env(ROOT / ".env")
import freya  # noqa: E402

BANK = "https://freya-bank-mock.freyavoice.workers.dev/v1"
OUT = pathlib.Path(__file__).resolve().parent / "spike_results.json"

PRE_DEFANG = """
vars["pre_hook_ran"] = "yes"
body = config.get("body") if isinstance(config, dict) else None
seen = ""
if isinstance(body, dict):
    seen = str(body.get("amount"))
vars["pre_hook_saw_amount"] = seen
if isinstance(config, dict):
    config["url"] = "https://example.invalid/defanged-write"
    config["method"] = "POST"
    fresh = {}
    fresh["spike"] = "defanged"
    fresh["customer_id"] = str(vars.get("customer_id") or "")
    config["body"] = fresh
vars["pre_defanged"] = "yes"
"""

POST_REWRITE = """
vars["post_hook_ran"] = "yes"
vars["post_marker"] = "post-ok"
if isinstance(result, dict):
    result.pop("header", None)
    data = result.get("data")
    if not isinstance(data, dict):
        data = {}
        result["data"] = data
    data["what_to_do_next"] = "SPIKE_POST_REWRITE"
    data["spike_rewritten"] = "yes"
"""

CODE_INC = """
raw = str(vars.get("code_runs") or "0")
n = 0
if raw.isdigit():
    n = int(raw)
n = n + 1
vars["code_runs"] = str(n)
vars["earlier_marker"] = "from-code-" + str(n)
"""

CLOSE_PRE = """
vars["close_pre_ran"] = "yes"
vars["close_pre_read"] = str(vars.get("earlier_marker") or "missing")
vars["close_pre_code_runs"] = str(vars.get("code_runs") or "missing")
vars["close_pre_post_marker"] = str(vars.get("post_marker") or "missing")
if isinstance(config, dict):
    body = {}
    body["customer_id"] = str(vars.get("customer_id") or "")
    body["outcome"] = "not_reached"
    body["reason"] = "unclear"
    body["spike_marker"] = str(vars.get("earlier_marker") or "missing")
    config["body"] = body
    config["url"] = "https://freya-bank-mock.freyavoice.workers.dev/v1/close_call"
    config["method"] = "POST"
"""

VARS = {
    "customer_id": "c_spike",
    "first_name": "Daniel",
    "last_name": "Reyes",
    "title": "Mr",
    "now": "2026-10-14T11:00",
}


def clean(obj):
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    return obj


def always(target):
    return {"target_node": target, "description": "always", "condition": {"operator": "always"}}


def eq(var, operand, target):
    return {
        "target_node": target,
        "description": var + " is " + operand,
        "condition": {"logic": "or", "conditions": [{"variable": var, "operator": "equals", "operand": operand}]},
    }


def code_node(name, source, transitions, default):
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": default,
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [
            {
                "type": "function",
                "function": {
                    "name": "code_exec",
                    "handler": "__function__:code_exec",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "default": source},
                            "timeout_ms": {"type": "number", "default": 10000},
                        },
                    },
                },
            }
        ],
        "transitions": transitions,
    }


def api_node(name, tool_name, url, body_props, pre, post, transitions, default, lifecycle=None):
    tool = {
        "type": "function",
        "config": {"timeoutMs": 8000, "maxRetries": 0},
        "function": {
            "name": tool_name,
            "handler": "__function__:api_call",
            "description": "Spike call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": url},
                    "method": {"type": "string", "default": "POST"},
                    "headers": {
                        "type": "object",
                        "properties": {"Content-Type": {"type": "string", "default": "application/json"}},
                    },
                    "body": {"type": "object", "properties": body_props, "required": []},
                },
            },
        },
        "pre_process_code": pre,
        "post_process_code": post,
    }
    if lifecycle:
        tool["config"]["alwaysRunsAt"] = lifecycle
        return tool
    return {
        "name": name,
        "node_type": "api_request",
        "passthrough": True,
        "passthrough_default_transition": default,
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [tool],
        "response_mapping": [{"path": "data.ok", "variable": name + "_ok"}],
        "transitions": transitions,
    }


def end_node(name):
    return {
        "name": name,
        "node_type": "end_call",
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "transitions": [],
        "terminal": {
            "type": "function",
            "function": {
                "name": "spike_end",
                "handler": "__function__:end_call",
                "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}},
            },
        },
    }


def promise_body():
    return {
        "customer_id": {"type": "string", "default": "{{customer_id}}"},
        "product_id": {"type": "string", "default": "p1"},
        "amount": {"type": "number", "default": 10},
        "date": {"type": "string", "default": "2026-10-14"},
    }


def close_body():
    return {
        "customer_id": {"type": "string", "default": "{{customer_id}}"},
        "outcome": {"type": "string", "default": "not_reached"},
        "reason": {"type": "string", "default": "unclear"},
    }


def hook_workflow():
    """Engine chain: code, write with hooks, re-enter the same code node, close_call."""
    return clean({
        "initial_node": "kick",
        "system_prompt": "You are a spike agent. Say one short sentence.",
        "test_variables": VARS,
        "nodes": {
            "kick": {
                "name": "kick",
                "node_type": "conversation",
                "passthrough": True,
                "passthrough_default_transition": "mark",
                "on_enter": {"type": "tts", "text": "One moment."},
                "task_message": "Say nothing else. No tool call is allowed.",
                "disable_inactivity_monitor": True,
                "transitions": [always("mark")],
            },
            "mark": code_node("mark", CODE_INC, [eq("code_runs", "1", "bridge"), always("write")], "write"),
            "bridge": {
                "name": "bridge",
                "node_type": "conversation",
                "passthrough": True,
                "passthrough_default_transition": "mark",
                "on_enter": {"type": "tts", "text": "Still here."},
                "task_message": "Say nothing else. No tool call is allowed.",
                "disable_inactivity_monitor": True,
                "transitions": [always("mark")],
            },
            "write": api_node(
                "write", "create_promise", BANK + "/create_promise", promise_body(),
                PRE_DEFANG, POST_REWRITE, [always("close_api")], "close_api",
            ),
            "close_api": api_node(
                "close_api", "close_call", BANK + "/close_call", close_body(),
                CLOSE_PRE, POST_REWRITE, [always("goodbye")], "goodbye",
            ),
            "goodbye": end_node("goodbye"),
        },
    })


def lifecycle_workflow():
    close_tool = api_node(
        "close_api", "close_call", BANK + "/close_call", close_body(),
        CLOSE_PRE, POST_REWRITE, [], "", lifecycle=["end_of_call"],
    )
    return clean({
        "initial_node": "mark",
        "system_prompt": "You are a spike agent.",
        "test_variables": VARS,
        "tools": [close_tool],
        "nodes": {
            "mark": code_node("mark", CODE_INC, [always("goodbye")], "goodbye"),
            "goodbye": end_node("goodbye"),
        },
    })


def guardrail_workflow():
    return clean({
        "initial_node": "hello",
        "system_prompt": "Say hello in one sentence.",
        "test_variables": VARS,
        "agent_config": {
            "llmConfig": {
                "provider": freya.AGENT_LLM["provider"],
                "model": freya.AGENT_LLM["model"],
                "temperature": 0.15,
            },
            "guardrailsConfig": {
                "enabled": True,
                "maxPerNTurns": {"max": 1, "n": 2},
            },
        },
        "nodes": {
            "hello": {
                "name": "hello",
                "node_type": "conversation",
                "passthrough": True,
                "passthrough_default_transition": "goodbye",
                "on_enter": {"type": "tts", "text": "Hello."},
                "task_message": "No tool call is allowed.",
                "max_node_turns": 2,
                "max_turn_transition": "goodbye",
                "tools": [
                    {
                        "type": "function",
                        "config": {"timeoutMs": 8000, "maxRetries": 0, "maxPerNTurns": 1},
                        "function": {
                            "name": "get_account",
                            "handler": "__function__:api_call",
                            "description": "Do not call this.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string", "default": BANK + "/get_account"},
                                    "method": {"type": "string", "default": "POST"},
                                    "body": {
                                        "type": "object",
                                        "properties": {"customer_id": {"type": "string", "default": "{{customer_id}}"}},
                                        "required": [],
                                    },
                                },
                            },
                        },
                    }
                ],
                "transitions": [always("goodbye")],
            },
            "goodbye": end_node("goodbye"),
        },
    })


def chat(workflow_id, message, call_id=None):
    body = {"workflowId": workflow_id, "message": message, "customerVariables": VARS}
    if call_id:
        body["callId"] = call_id
    return freya.api("POST", "/api/v2/chat/message", body)


def end_chat(call_id):
    if not call_id:
        return
    try:
        freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
    except Exception as exc:
        return str(exc)


def dump_call(call_id):
    if not call_id:
        return {}
    try:
        return freya.get_call(call_id)
    except Exception as exc:
        return {"error": str(exc)[:800]}


def blob(call):
    try:
        return json.dumps(call)[:200000]
    except Exception:
        return str(call)[:20000]


def node_enters(call):
    found = []
    for msg in call.get("messages") or []:
        if msg.get("role") != "system":
            continue
        text = msg.get("message") or ""
        if "node_enter" in text or "function_call" in text:
            found.append(text[:1500])
    return found


def saved_workflow(workflow_id):
    try:
        packed = freya.api("GET", "/api/v2/workflow/" + workflow_id, workspaceId=freya.workspace_id())
        wf = (packed.get("workflow") or {}).get("workflow") or packed.get("workflow") or packed
        return wf if isinstance(wf, dict) else {"raw_type": str(type(wf))}
    except Exception as exc:
        return {"error": str(exc)[:800]}


def find_hooks(wf):
    hits = []
    nodes = (wf or {}).get("nodes") or {}
    if not isinstance(nodes, dict):
        return hits
    for name, node in nodes.items():
        for action in node.get("pre_actions") or []:
            hits.append({
                "node": name,
                "tool": (action.get("function") or {}).get("name"),
                "has_pre": bool(action.get("pre_process_code")),
                "has_post": bool(action.get("post_process_code")),
                "pre_has_defang": "example.invalid" in (action.get("pre_process_code") or ""),
            })
    return hits


def run_chat_case(workflow, message="hello"):
    saved = freya.save_workflow("spike", workflow)
    info = {"saved": saved}
    try:
        turn = chat(saved["workflow_id"], message)
        info["chat_keys"] = sorted(turn.keys()) if isinstance(turn, dict) else str(type(turn))
        call_id = ""
        response = ""
        if isinstance(turn, dict):
            call_id = turn.get("callId") or turn.get("call_id") or ""
            response = str(turn.get("response") or "")[:500]
        info["call_id"] = call_id
        info["response"] = response
        call = dump_call(call_id)
        info["endedReason"] = call.get("endedReason")
        text = blob(call)
        info["markers"] = {
            "defanged_url": "example.invalid" in text,
            "post_rewrite": "SPIKE_POST_REWRITE" in text,
            "pre_hook_ran": "pre_hook_ran" in text or "pre_defanged" in text,
            "from_code_1": "from-code-1" in text,
            "from_code_2": "from-code-2" in text,
            "close_pre_read": "close_pre_read" in text,
            "close_marker_in_body": "spike_marker" in text,
            "code_runs_2": '"code_runs": "2"' in text or "'code_runs': '2'" in text or "code_runs" in text,
        }
        enters = node_enters(call)
        info["event_count"] = len(enters)
        info["events_head"] = enters[:12]
        mark_enters = [e for e in enters if '"mark"' in e or "mark" in e]
        info["mark_event_count"] = len(mark_enters)
        info["stored_hooks"] = find_hooks(saved_workflow(saved["workflow_id"]))
        end_chat(call_id)
    except Exception as exc:
        info["error"] = str(exc)[:1500]
        info["trace"] = traceback.format_exc()[-1500:]
    return info


def main():
    results = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "bank": BANK,
        "tests": {},
        "fallbacks": {},
    }
    if not os.environ.get("FREYA_API_KEY"):
        results["error"] = "FREYA_API_KEY missing"
        OUT.write_text(json.dumps(results, indent=1))
        print(json.dumps(results, indent=1))
        return 1
    try:
        print("hook chain", flush=True)
        results["tests"]["hooks_and_code"] = run_chat_case(hook_workflow())
        print("lifecycle close", flush=True)
        results["tests"]["lifecycle_close"] = run_chat_case(lifecycle_workflow())
        print("guardrails save", flush=True)
        guard_saved = freya.save_workflow("spike", guardrail_workflow())
        stored = saved_workflow(guard_saved["workflow_id"])
        ac = stored.get("agent_config") if isinstance(stored, dict) else {}
        if not isinstance(ac, dict):
            ac = {}
        llm = ac.get("llmConfig") if isinstance(ac.get("llmConfig"), dict) else {}
        tool_cfg = None
        hello = (stored.get("nodes") or {}).get("hello") if isinstance(stored.get("nodes"), dict) else {}
        tools = (hello or {}).get("tools") or []
        if tools:
            tool_cfg = tools[0].get("config")
        results["tests"]["guardrails_and_temperature"] = {
            "saved": guard_saved,
            "agent_config_keys": sorted(ac.keys()),
            "llmConfig": llm,
            "temperature_stored": llm.get("temperature"),
            "guardrailsConfig": ac.get("guardrailsConfig"),
            "guardrails_stored": "guardrailsConfig" in ac and ac.get("guardrailsConfig") not in (None, {}),
            "tool_config": tool_cfg,
            "maxPerNTurns_on_tool": (tool_cfg or {}).get("maxPerNTurns") if isinstance(tool_cfg, dict) else None,
            "maxPerNTurns_in_guardrails": (ac.get("guardrailsConfig") or {}).get("maxPerNTurns") if isinstance(ac.get("guardrailsConfig"), dict) else None,
        }
        print("temperature simulation", flush=True)
        try:
            persona = freya.create_personas([{
                "name": "spike temp",
                "prompt": "Say hello in one sentence and then say goodbye.",
                "successCriteria": "The agent speaks.",
                "tags": ["spike"],
            }])
            pid = persona["personas"][0]["id"]
            sim_id, it_id, status = freya.run_simulation(
                guard_saved["agent_id"], "spike-temperature", {pid: dict(VARS)}, max_seconds=90, concurrency=1,
            )
            iteration = freya.get_iteration(sim_id, it_id)
            runs = iteration.get("runs") or []
            call_id = runs[0].get("callId") if runs else ""
            call = dump_call(call_id)
            text = blob(call)
            results["tests"]["temperature_simulation"] = {
                "simulation_id": sim_id,
                "iteration_id": it_id,
                "status": status,
                "call_id": call_id,
                "temperature_present_in_call": "temperature" in text.lower(),
                "temperature_0_15": "0.15" in text,
                "override_was": freya.AGENT_LLM,
                "call_keys": sorted(call.keys()) if isinstance(call, dict) else [],
            }
        except Exception as exc:
            results["tests"]["temperature_simulation"] = {"error": str(exc)[:1500]}
    except Exception as exc:
        results["error"] = str(exc)[:1500]
        results["trace"] = traceback.format_exc()[-2000:]
    hooks = results["tests"].get("hooks_and_code") or {}
    markers = hooks.get("markers") or {}
    life = results["tests"].get("lifecycle_close") or {}
    life_markers = life.get("markers") or {}
    guard = results["tests"].get("guardrails_and_temperature") or {}
    temp = results["tests"].get("temperature_simulation") or {}
    pre_ok = bool(markers.get("defanged_url"))
    post_ok = bool(markers.get("post_rewrite"))
    rerun_ok = bool(markers.get("from_code_2")) or hooks.get("mark_event_count", 0) >= 2
    close_ok = bool(markers.get("close_pre_read") or markers.get("close_marker_in_body") or markers.get("from_code_1"))
    life_ok = bool(life_markers.get("close_pre_read") or life_markers.get("close_marker_in_body"))
    results["conclusions"] = {
        "pre_hook_can_defang": pre_ok,
        "post_hook_can_rewrite": post_ok,
        "code_node_reruns_mid_call": rerun_ok,
        "close_pre_hook_reads_earlier_vars": close_ok,
        "lifecycle_close_hook_reads_vars": life_ok,
        "guardrailsConfig_stored": bool(guard.get("guardrails_stored")),
        "maxPerNTurns_stored": guard.get("maxPerNTurns_on_tool") is not None or guard.get("maxPerNTurns_in_guardrails") is not None,
        "temperature_stored_on_workflow": guard.get("temperature_stored") == 0.15 or guard.get("temperature_stored") == "0.15",
        "temperature_seen_after_sim_override": bool(temp.get("temperature_0_15")),
    }
    c = results["conclusions"]
    results["fallbacks"] = {
        "writes": "pre_hook defang" if c["pre_hook_can_defang"] else "gate-before-tool: only an api_request node reached after code sets baked fields may call a write; do not give the dialogue model the write tool",
        "tool_results": "post_hook what_to_do_next" if c["post_hook_can_rewrite"] else "code node writes say_line; fixed TTS speaks it; do not trust the model to read the raw tool result",
        "code": "re-enter code nodes" if c["code_node_reruns_mid_call"] else "chain a fresh code node for every step; do not rely on re-entry",
        "close": "in-graph close_call api_request with baked outcome plus pre_hook" if not c["lifecycle_close_hook_reads_vars"] else "lifecycle end_of_call close_call whose pre_hook computes the outcome",
        "guardrails": "do not depend on guardrailsConfig or maxPerNTurns; use max_node_turns and max_turn_transition" if not c["guardrailsConfig_stored"] else "guardrailsConfig was stored; still keep max_node_turns circuit breakers",
        "temperature": "leave agent_config out of the saved workflow; simulation overrides replace the model and temperature is not a control we keep",
    }
    OUT.write_text(json.dumps(results, indent=1))
    print(json.dumps({"conclusions": results["conclusions"], "fallbacks": results["fallbacks"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
