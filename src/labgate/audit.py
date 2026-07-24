"""Append-only audit log (requirement F9).

One JSONL file per platform instance. Every state-changing action lands
here with actor identity and ISO timestamp. Never rotated or rewritten by
the platform itself.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, storage_dir: Path) -> None:
        storage_dir.mkdir(parents=True, exist_ok=True)
        self._path = storage_dir / "audit.jsonl"
        self._lock = threading.Lock()

    def append(self, event_type: str, actor: str, payload: dict | None = None) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "actor": actor,
            "payload": payload or {},
        }
        line = json.dumps(record, default=str)
        with self._lock, self._path.open("a") as f:
            f.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines() if line]
