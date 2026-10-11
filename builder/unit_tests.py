"""Freya unit-test and chat-fork helpers for fast regression."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def tool_call_assertion(tool: str, args: Optional[Dict] = None, *, exact: bool = False, negate: bool = False) -> Dict[str, Any]:
    a: Dict[str, Any] = {"type": "tool_call", "tool": tool, "negate": negate, "argsMatch": "exact" if exact else "subset"}
    if args is not None:
        a["args"] = args
    return a


def routing_assertion(target: str, *, signal: str = "node_entered", negate: bool = False) -> Dict[str, Any]:
    return {"type": "routing", "signal": signal, "target": target, "negate": negate}


def extraction_assertion(name: str, *, match: str = "exists", value: Any = None, min_confidence: float = 0.5) -> Dict[str, Any]:
    a: Dict[str, Any] = {"type": "extraction", "name": name, "match": match, "minConfidence": min_confidence}
    if value is not None:
        a["value"] = value
    return a


def make_unit_test(
    workspace_id: str,
    entity_id: str,
    name: str,
    messages: List[Dict[str, Any]],
    assertions: List[Dict[str, Any]],
    *,
    entity_type: str = "workflow",
    customer_variables: Optional[Dict] = None,
    expectation: str = "",
) -> Dict[str, Any]:
    return {
        "workspaceId": workspace_id,
        "entityType": entity_type,
        "entityId": entity_id,
        "name": name,
        "expectation": expectation or name,
        "assertions": assertions,
        "input": {
            "schemaVersion": 1,
            "callType": "web",
            "messages": messages,
            "customerVariables": customer_variables or {},
        },
        "judgeContext": {
            "includeRoutingEvents": True,
            "includeExtractionEvents": True,
            "includeToolCalls": True,
            "includeOtherEvents": True,
        },
    }


def create_unit_test(body: Dict[str, Any]) -> Dict[str, Any]:
    import freya

    return freya.api("POST", "/api/v2/unit-tests", body)


def run_all_unit_tests(workspace_id: str, entity_type: str, entity_id: str) -> Dict[str, Any]:
    import freya

    return freya.api(
        "POST",
        "/api/v2/unit-tests/run-all",
        {"workspaceId": workspace_id, "entityType": entity_type, "entityId": entity_id},
    )


def chat_turn(workflow_id: str, message: str, *, call_id: Optional[str] = None, customer_variables: Optional[Dict] = None, from_message_id: Optional[str] = None) -> Dict[str, Any]:
    import freya

    body: Dict[str, Any] = {"workflowId": workflow_id, "message": message}
    if call_id:
        body["callId"] = call_id
    if customer_variables is not None:
        body["customerVariables"] = customer_variables
    if from_message_id:
        body["fromMessageId"] = from_message_id
    return freya.api("POST", "/api/v2/chat/message", body)


def chat_end(call_id: str) -> Dict[str, Any]:
    import freya

    return freya.api("POST", "/api/v2/chat/end", {"callId": call_id})


def chat_fork(call_id: str, from_message_id: str, **kwargs) -> Dict[str, Any]:
    import freya

    body = {"callId": call_id, "fromMessageId": from_message_id, **kwargs}
    return freya.api("POST", "/api/v2/chat/fork", body)


# Seed assertions useful for Brief A happy path
def brief_a_unit_suite(workspace_id: str, workflow_id: str, customer_id: str = "c_unit_test") -> List[Dict[str, Any]]:
    vars_ = {
        "customer_id": customer_id,
        "first_name": "Daniel",
        "last_name": "Reyes",
        "title": "Mr",
        "now": "2026-10-14T11:00",
    }
    return [
        make_unit_test(
            workspace_id,
            workflow_id,
            "opening does not call get_account",
            [{"role": "user", "message": "Hello?"}],
            [tool_call_assertion("get_account", negate=True), tool_call_assertion("create_promise", negate=True)],
            customer_variables=vars_,
            expectation="Before identity, account tools are not called",
        ),
        make_unit_test(
            workspace_id,
            workflow_id,
            "confirm yes moves toward identity tools only after confirm",
            [
                {"role": "user", "message": "Yes, speaking."},
            ],
            [tool_call_assertion("create_promise", negate=True)],
            customer_variables=vars_,
            expectation="No promise before verification and confirmation",
        ),
    ]
