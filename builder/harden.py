"""Post-save hardening: lower temperature and attach guardrailsConfig from policy never_say."""
from __future__ import annotations

import re
from typing import Any, Dict, List


def guardrails_from_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    never = policy.get("never_say") or []
    # Build a single regex of forbidden offer phrases (output regenerate)
    escaped = [re.escape(p) for p in never if p]
    pattern = "(?i)(" + "|".join(escaped) + ")" if escaped else "(?i)discount|waiver"
    return {
        "guardrails_enabled": True,
        "custom_rules_enabled": True,
        "rules": [
            {
                "id": "never-say-offers",
                "type": "regex",
                "pattern": pattern,
                "action": "regenerate",
                "enabled": True,
                "description": "Regenerate replies that offer forbidden terms from the brief",
                "case_sensitive": False,
            }
        ],
        "max_regenerate_retries": 2,
        "input_config": {
            "enabled": True,
            "normalize": True,
            "detect_injection": True,
            "detect_weird_input": True,
            "run_classifier": False,
        },
    }


def apply_hardening(workflow_id: str, policy: Dict[str, Any], *, temperature: float = 0.2) -> Dict[str, Any]:
    """PUT agent_config with temperature + guardrails, then publish. Returns what stuck."""
    import freya

    ws = freya.workspace_id()
    stored = freya.api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=ws)["workflow"]["workflow"]
    ac = dict(stored.get("agent_config") or {})
    llm = dict(ac.get("llmConfig") or {})
    llm["temperature"] = temperature
    # Keep event model
    llm["provider"] = freya.AGENT_LLM["provider"]
    llm["model"] = freya.AGENT_LLM["model"]
    ac["llmConfig"] = llm
    ac["guardrailsConfig"] = guardrails_from_policy(policy)
    freya.api("PUT", f"/api/v2/workflow/{workflow_id}", {"workflow": {**stored, "agent_config": ac}})
    vs = freya.api("GET", f"/api/v2/versions/workflow/{workflow_id}", workspaceId=ws)["versions"]
    newest = max(vs, key=lambda v: v["versionNumber"])
    if not newest.get("isPublished"):
        freya.api("POST", f"/api/v2/versions/workflow/{workflow_id}/publish", {"versionId": newest["id"]})
    stored2 = freya.api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=ws)["workflow"]["workflow"]
    ac2 = stored2.get("agent_config") or {}
    return {
        "temperature": (ac2.get("llmConfig") or {}).get("temperature"),
        "guardrails_present": bool(ac2.get("guardrailsConfig")),
        "version": newest.get("versionNumber"),
    }
