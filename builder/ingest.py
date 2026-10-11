"""Split brief files into cited chunks for Policy IR extraction."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, List


def _chunk_id(file: str, idx: int, text: str) -> str:
    h = hashlib.sha1(f"{file}:{idx}:{text[:80]}".encode()).hexdigest()[:8]
    stem = Path(file).stem.replace(" ", "_")
    return f"{stem}-c{idx:03d}-{h}"


def ingest_briefs(paths: List[str | Path]) -> Dict:
    """Return {source_chunks: [...], files: [...]} with memo supersession hints."""
    chunks = []
    files = []
    for p in paths:
        path = Path(p)
        text = path.read_text()
        files.append(str(path))
        # Split on markdown headings and numbered rules
        parts = re.split(r"(?m)(?=^#{1,3} |\n\d+\. )", text)
        parts = [x.strip() for x in parts if x and x.strip()]
        if len(parts) <= 1:
            # paragraph fallback
            parts = [x.strip() for x in re.split(r"\n\n+", text) if x.strip()]
        for i, part in enumerate(parts):
            cid = _chunk_id(path.name, i, part)
            entry = {"id": cid, "file": path.name, "text": part, "supersedes": []}
            # Heuristic: memos / "update" / "replaces" mark supersession of earlier same-file chunks
            low = part.lower()
            if any(w in low for w in ("supersedes", "replaces rule", "instead of", "amendment", "memo:")):
                # supersede previous chunks from same file that share rule numbers mentioned
                nums = re.findall(r"rule\s+(\d+)", low)
                for prev in chunks:
                    if prev["file"] != path.name:
                        continue
                    for n in nums:
                        if re.search(rf"(?m)^{n}\. |rule {n}\b", prev["text"], re.I):
                            entry["supersedes"].append(prev["id"])
            chunks.append(entry)
    return {"source_chunks": chunks, "files": files}


def apply_supersession(chunks: List[Dict]) -> List[Dict]:
    """Mark superseded chunks; return active chunks (superseded ones kept but flagged)."""
    dead = set()
    for c in chunks:
        for sid in c.get("supersedes") or []:
            dead.add(sid)
    for c in chunks:
        c["active"] = c["id"] not in dead
    return chunks
