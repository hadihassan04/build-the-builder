"""API tool builders for Freya workflows."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from builder.guards import (
    add_note_pre_process,
    close_call_pre_process,
    create_promise_pre_process,
    schedule_callback_pre_process,
    transfer_pre_process,
)
from builder.guards.policy_gate import close_call_post_process
from builder.guards.post_hooks import (
    add_note_post_hook,
    create_promise_post_hook,
    generic_post_hook,
    get_account_post_hook,
    schedule_callback_post_hook,
    transfer_post_hook,
    verify_identity_post_hook,
)


BANK = "https://freya-bank-mock.freyavoice.workers.dev/v1"


def _api_tool(
    name: str,
    description: str,
    body_fields: Dict[str, Dict[str, Any]],
    required: List[str],
    *,
    pre: Optional[str] = None,
    post: Optional[str] = None,
    timeout_ms: int = 12000,
    max_retries: int = 0,
    max_per_n_turns: Optional[int] = None,
    always_runs_at: Optional[List[str]] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    props: Dict[str, Any] = {
        "url": {"type": "string", "default": url or f"{BANK}/{name}"},
        "method": {"type": "string", "default": "POST"},
        "headers": {
            "type": "object",
            "properties": {
                "Content-Type": {"type": "string", "default": "application/json"}
            },
        },
        "body": {
            "type": "object",
            "required": required,
            "properties": body_fields,
        },
    }
    cfg: Dict[str, Any] = {"timeoutMs": timeout_ms, "maxRetries": max_retries}
    if max_per_n_turns is not None:
        cfg["maxPerNTurns"] = max_per_n_turns
    if always_runs_at:
        cfg["alwaysRunsAt"] = always_runs_at
    tool: Dict[str, Any] = {
        "type": "function",
        "config": cfg,
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description,
            "parameters": {"type": "object", "properties": props},
        },
    }
    if pre:
        tool["pre_process_code"] = pre
    if post:
        tool["post_process_code"] = post
    return tool


def end_call_tool() -> Dict[str, Any]:
    return {
        "type": "function",
        "config": {"timeoutMs": 10000},
        "function": {
            "name": "end_call",
            "handler": "__function__:end_call",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_mode": {"type": "string", "default": "none"},
                    "interruptible": {"type": "boolean", "default": True},
                    "trigger_pattern": {
                        "type": "string",
                        "default": "Have a good day|have a good day",
                    },
                    "trigger_timeout": {"type": "number", "default": 3},
                    "trigger_pattern_both": {
                        "type": "string",
                        "default": "goodbye|bye|have a good day|you too|thank you|thanks|take care",
                    },
                },
            },
            "description": "Ends the call. Prefer saying Have a good day. so the trigger ends it. Call only if needed after goodbye.",
        },
    }


def verify_identity_tool(tools_ir: Dict[str, Any]) -> Dict[str, Any]:
    field_enum = (((tools_ir.get("by_name") or {}).get("verify_identity") or {}).get("fields") or {}).get("field", {}).get("enum") or [
        "date_of_birth",
        "phone_last4",
    ]
    return _api_tool(
        "verify_identity",
        "Check one security answer. CALL it once for every answer the customer gives.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "field": {
                "type": "string",
                "enum": field_enum,
                "default": "{{field}}",
                "description": "date_of_birth or phone_last4",
            },
            "answer": {
                "type": "string",
                "default": "{{answer}}",
                "description": "date_of_birth as YYYY-MM-DD; phone_last4 as four digits. Read numeric dates as day-month-year.",
            },
        },
        ["field", "answer"],
        post=verify_identity_post_hook(),
        url=((tools_ir.get("by_name") or {}).get("verify_identity") or {}).get("url"),
    )


def get_account_tool(tools_ir: Dict[str, Any]) -> Dict[str, Any]:
    return _api_tool(
        "get_account",
        "Load late products. CALL once after identity is verified, before saying anything about the account.",
        {"customer_id": {"type": "string", "default": "{{customer_id}}"}},
        [],
        post=get_account_post_hook(),
        max_retries=2,
        url=((tools_ir.get("by_name") or {}).get("get_account") or {}).get("url"),
    )


def create_promise_tool(tools_ir: Dict[str, Any]) -> Dict[str, Any]:
    return _api_tool(
        "create_promise",
        "Record a payment promise. CALL only after the customer clearly confirmed the read-back of product, amount and date.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "product_id": {
                "type": "string",
                "default": "{{product_id}}",
                "description": "Exact product_id from get_account (p1, p2, ...). Never invent.",
            },
            "amount": {
                "type": "number",
                "default": "{{amount}}",
                "description": "Confirmed amount.",
            },
            "date": {
                "type": "string",
                "default": "{{date}}",
                "description": "Confirmed payment date YYYY-MM-DD.",
            },
        },
        ["product_id", "amount", "date"],
        pre=create_promise_pre_process(),
        post=create_promise_post_hook(),
        max_per_n_turns=2,
        url=((tools_ir.get("by_name") or {}).get("create_promise") or {}).get("url"),
    )


def add_note_tool(tools_ir: Dict[str, Any], reasons: List[str]) -> Dict[str, Any]:
    return _api_tool(
        "add_note",
        "Record why a product ended without a promise. CALL once per such product. Never on a transfer path.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "product_id": {
                "type": "string",
                "default": "{{product_id}}",
                "description": "product_id from get_account",
            },
            "reason": {
                "type": "string",
                "enum": reasons,
                "default": "{{reason}}",
                "description": "One of: " + ", ".join(reasons),
            },
            "text": {"type": "string", "default": "{{text}}", "description": "Optional short note"},
        },
        ["product_id", "reason"],
        pre=add_note_pre_process(),
        post=add_note_post_hook(),
        url=((tools_ir.get("by_name") or {}).get("add_note") or {}).get("url"),
    )


def schedule_callback_tool(tools_ir: Dict[str, Any]) -> Dict[str, Any]:
    return _api_tool(
        "schedule_callback",
        "Book a callback when a third party answers. CALL only with a date and time inside the allowed window.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "date": {"type": "string", "default": "{{date}}", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "default": "{{time}}", "description": "HH:MM"},
        },
        ["date", "time"],
        pre=schedule_callback_pre_process(),
        post=schedule_callback_post_hook(),
        url=((tools_ir.get("by_name") or {}).get("schedule_callback") or {}).get("url"),
    )


def transfer_tool(tools_ir: Dict[str, Any], queues: List[str]) -> Dict[str, Any]:
    return _api_tool(
        "transfer_to_agent",
        "Hand the call to a person. CALL with the correct queue only when the brief requires a transfer.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "queue": {
                "type": "string",
                "enum": queues,
                "default": "{{queue}}",
                "description": "One of: " + ", ".join(queues),
            },
        },
        ["queue"],
        pre=transfer_pre_process(),
        post=transfer_post_hook(),
        url=((tools_ir.get("by_name") or {}).get("transfer_to_agent") or {}).get("url"),
    )


def close_call_request(tools_ir: Dict[str, Any], outcomes: List[str]) -> Dict[str, Any]:
    """In-graph close_call. Same gate as the lifecycle tool, without alwaysRunsAt."""
    tool = close_call_lifecycle(tools_ir, outcomes)
    cfg = dict(tool.get("config") or {})
    cfg.pop("alwaysRunsAt", None)
    tool["config"] = cfg
    return tool


def close_call_lifecycle(tools_ir: Dict[str, Any], outcomes: List[str]) -> Dict[str, Any]:
    return _api_tool(
        "close_call",
        "Record how the call ended. Engine fires at end of call; outcome is filled from resolved_outcome.",
        {
            "customer_id": {"type": "string", "default": "{{customer_id}}"},
            "outcome": {
                "type": "string",
                "enum": outcomes,
                "default": "{{resolved_outcome}}",
                "description": "Final outcome",
            },
            "reason": {
                "type": "string",
                "default": "{{resolved_reason}}",
                "description": "Reason when required",
            },
        },
        ["outcome"],
        pre=close_call_pre_process(),
        post=close_call_post_process(),
        always_runs_at=["end_of_call"],
        url=((tools_ir.get("by_name") or {}).get("close_call") or {}).get("url"),
    )
