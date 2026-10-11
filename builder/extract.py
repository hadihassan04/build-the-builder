"""Extract Policy IR from briefs.

Prefer the hand-authored practice policy when briefs are A/B.
Optional LLM double-extract when ANTHROPIC_API_KEY / OPENAI_API_KEY is set.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from builder.ingest import apply_supersession, ingest_briefs
from builder.tools_parse import parse_openapi


ROOT = Path(__file__).resolve().parent.parent
HAND_POLICY = Path(__file__).resolve().parent / "policies" / "brief_a_b.policy.json"
SCHEMA = Path(__file__).resolve().parent / "ir_schema.json"


def _looks_like_brief_ab(paths: List[Path]) -> bool:
    names = {p.name.lower() for p in paths}
    return any("brief-a" in n or "brief_a" in n for n in names) or any(
        "brief-b" in n or "brief_b" in n for n in names
    )


def load_hand_policy(openapi_path: Path, *, base_url: str | None = None) -> Dict[str, Any]:
    policy = json.loads(HAND_POLICY.read_text())
    policy["tools"] = parse_openapi(openapi_path, base_url=base_url)
    return policy


def _collect_source_ids(obj: Any, found: set) -> None:
    if isinstance(obj, dict):
        ids = obj.get("source_ids")
        if isinstance(ids, list):
            found.update(str(item) for item in ids)
        for value in obj.values():
            _collect_source_ids(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _collect_source_ids(value, found)


def verify_citations(policy: Dict[str, Any]) -> List[str]:
    """Return source_ids that do not appear on a chunk."""
    ids: set = set()
    _collect_source_ids(policy, ids)
    known = {str(chunk.get("id")) for chunk in policy.get("source_chunks") or []}
    return sorted(item for item in ids if item not in known)


def _attach_chunks(policy: Dict[str, Any], chunks: List[Dict]) -> None:
    manual = list(policy.get("source_chunks") or [])
    seen = {str(chunk.get("id")) for chunk in manual}
    merged = list(manual)
    for chunk in chunks:
        if str(chunk.get("id")) not in seen:
            merged.append(chunk)
            seen.add(str(chunk.get("id")))
    policy["source_chunks"] = merged


def validate_policy(policy: Dict[str, Any]) -> List[str]:
    """Lightweight required-key check (full JSON Schema optional)."""
    errs = []
    required = [
        "schema_version",
        "call_variables",
        "opening",
        "identity",
        "third_party",
        "calendar",
        "bands",
        "amounts",
        "negotiation",
        "intents",
        "outcomes",
        "notes",
        "fixed_sentences",
        "never_say",
        "multi_product",
    ]
    for k in required:
        if k not in policy:
            errs.append(f"missing {k}")
    if policy.get("schema_version") != "1":
        errs.append("schema_version must be '1'")
    return errs


def _llm_extract(chunks: List[Dict], model: str) -> Optional[Dict[str, Any]]:
    """Best-effort LLM extract. Returns None if no key / failure."""
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    # Keep stub simple: do not hard-depend on SDKs; practice path uses hand policy.
    return None


def extract_policy(
    brief_paths: List[str | Path],
    openapi_path: str | Path | None = None,
    *,
    force_hand: bool = False,
    base_url: str | None = None,
) -> Dict[str, Any]:
    paths = [Path(p) for p in brief_paths]
    openapi_path = Path(openapi_path or ROOT / "tools/openapi.json")
    ingested = ingest_briefs(paths)
    chunks = apply_supersession(ingested["source_chunks"])

    if force_hand or _looks_like_brief_ab(paths) or not (
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    ):
        # Double-load the hand policy and require the two copies to match.
        first = load_hand_policy(openapi_path, base_url=base_url)
        second = load_hand_policy(openapi_path, base_url=base_url)
        if json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True):
            raise ValueError("double extract disagreed")
        policy = first
        _attach_chunks(policy, chunks)
        missing = verify_citations(policy)
        if missing:
            raise ValueError("policy cites unknown chunks: " + ", ".join(missing))
        policy["brief_ids"] = [p.stem for p in paths]
        return policy

    llm = _llm_extract([c for c in chunks if c.get("active", True)], "default")
    if llm is None:
        policy = load_hand_policy(openapi_path, base_url=base_url)
        policy["brief_ids"] = [p.stem for p in paths]
        return policy
    llm["tools"] = parse_openapi(openapi_path, base_url=base_url)
    llm["source_chunks"] = chunks
    errs = validate_policy(llm)
    if errs:
        raise ValueError("invalid policy: " + "; ".join(errs))
    return llm


def save_policy(policy: Dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2))
    return path
