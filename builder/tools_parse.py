"""Deterministic parser for the bank's tools/openapi.json. Never use an LLM for this."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def normalize_mock_base(url: str | None) -> str:
    """Turn a Workers root or …/v1 URL into the openapi server base (…/v1)."""
    if not url:
        return ""
    base = url.strip().rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


def parse_openapi(path: str | Path, *, base_url: str | None = None) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text())
    servers = data.get("servers") or []
    discovered = (servers[0].get("url") if servers else "").rstrip("/")
    base = normalize_mock_base(base_url) if base_url else discovered
    by_name: Dict[str, Any] = {}
    for path_key, methods in (data.get("paths") or {}).items():
        for method, op in (methods or {}).items():
            name = path_key.strip("/").split("/")[-1]
            body_schema = (
                (((op.get("requestBody") or {}).get("content") or {})
                 .get("application/json") or {}).get("schema")
                or {}
            )
            props = body_schema.get("properties") or {}
            required = list(body_schema.get("required") or [])
            fields = {}
            for fname, fschema in props.items():
                fields[fname] = {
                    "type": fschema.get("type"),
                    "enum": fschema.get("enum"),
                    "format": fschema.get("format"),
                    "description": fschema.get("description"),
                    "required": fname in required,
                }
            resp_schema = (
                ((((op.get("responses") or {}).get("200") or {}).get("content") or {})
                 .get("application/json") or {}).get("schema")
                or {}
            )
            by_name[name] = {
                "name": name,
                "method": method.upper(),
                "path": path_key,
                "url": base + path_key,
                "summary": op.get("summary") or "",
                "fields": fields,
                "required": required,
                "response_properties": (resp_schema.get("properties") or {}),
            }
    return {
        "base_url": base,
        "names": sorted(by_name.keys()),
        "by_name": by_name,
    }


def enum_for(tools: Dict[str, Any], tool: str, field: str) -> Optional[List[str]]:
    f = ((tools.get("by_name") or {}).get(tool) or {}).get("fields") or {}
    return (f.get(field) or {}).get("enum")
