"""Builder run trace (required for submission)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


class Trace:
    def __init__(self, out_dir: str | Path = "traces"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.start = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.path = self.out_dir / (time.strftime("%Y%m%d-%H%M%S") + "-builder.jsonl")
        self._fh = self.path.open("w")
        self.log({"event": "start", "time": self.start})

    def log(self, obj: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(obj, default=str) + "\n")
        self._fh.flush()

    def model_call(self, model: str, purpose: str, **extra) -> None:
        self.log({"event": "model_call", "model": model, "purpose": purpose, **extra})

    def freya(self, action: str, **extra) -> None:
        self.log({"event": "freya", "action": action, **extra})

    def test(self, name: str, **extra) -> None:
        self.log({"event": "test", "name": name, **extra})

    def close(self, **extra) -> Path:
        self.log({"event": "end", "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **extra})
        self._fh.close()
        return self.path
