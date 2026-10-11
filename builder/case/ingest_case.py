"""Ingest a case folder: locate the brief, the service contract, the test data and all source material.

Generic: files are classified by content, not by name.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

TEXT_EXT = {".md", ".txt", ".markdown", ".json", ".toml", ".js"}


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _looks_like_contract(text: str) -> bool:
    fences = len(re.findall(r"```", text))
    return fences >= 10 and "POST" in text and '"data"' in text and '"result"' in text


def _looks_like_test_data(path: str) -> Optional[Dict[str, Any]]:
    if not path.endswith(".json"):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "services" in v[0]:
                return d
    return None


def ingest_case(folder: str) -> Dict[str, Any]:
    folder = os.path.abspath(folder)
    files: List[str] = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for n in names:
            if n.startswith("."):
                continue
            files.append(os.path.join(root, n))
    files.sort()
    case: Dict[str, Any] = {
        "folder": folder,
        "brief": None,
        "contract": None,
        "test_data": None,
        "test_data_path": None,
        "sources": [],
        "pdfs": [],
        "mock_examples": [],
        "all_files": [os.path.relpath(p, folder) for p in files],
    }
    best_brief = None
    for p in files:
        rel = os.path.relpath(p, folder)
        ext = os.path.splitext(p)[1].lower()
        if ext == ".pdf":
            case["pdfs"].append(p)
            continue
        if ext not in TEXT_EXT:
            continue
        td = _looks_like_test_data(p)
        if td is not None:
            case["test_data"] = td
            case["test_data_path"] = p
            continue
        text = _read(p)
        if ext == ".json":
            continue
        if "cloudflare" in rel.lower() or "mock" in rel.lower():
            case["mock_examples"].append({"path": rel, "text": text})
            continue
        if _looks_like_contract(text) and case["contract"] is None:
            case["contract"] = {"path": rel, "text": text}
            case["sources"].append({"path": rel, "text": text})
            continue
        if os.path.basename(p).lower() in ("brief.md", "readme.md") and os.path.dirname(p) == folder:
            if best_brief is None or os.path.basename(p).lower() == "brief.md":
                best_brief = {"path": rel, "text": text}
        case["sources"].append({"path": rel, "text": text})
    case["brief"] = best_brief
    return case


def pdf_text(path: str) -> str:
    """Best-effort PDF text (pdftotext if present, else crude stream scrape)."""
    try:
        import subprocess
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    except Exception:
        pass
    try:
        raw = open(path, "rb").read()
        parts = re.findall(rb"\(([^()\\]{2,200})\)\s*Tj", raw)
        txt = " ".join(p.decode("latin-1", "replace") for p in parts)
        return txt
    except Exception:
        return ""


if __name__ == "__main__":
    import sys
    c = ingest_case(sys.argv[1])
    print(json.dumps({k: (v if k in ("folder", "all_files", "test_data_path") else (v["path"] if isinstance(v, dict) and "path" in v else (len(v) if isinstance(v, list) else bool(v)))) for k, v in c.items()}, indent=1, ensure_ascii=False))
