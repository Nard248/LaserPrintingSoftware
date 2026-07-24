"""Run results: telemetry events + binary artifacts (requirement F8)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class RunResults:
    def __init__(self, storage_dir: Path, plan_id: str) -> None:
        self._dir = storage_dir / "runs" / plan_id
        self._artifacts = self._dir / "artifacts"
        self._artifacts.mkdir(parents=True, exist_ok=True)
        self._events_path = self._dir / "telemetry.jsonl"
        self._lock = threading.Lock()

    def event(self, event_type: str, payload: dict | None = None) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "payload": payload or {},
        }
        with self._lock, self._events_path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def save_artifact(self, name: str, data: bytes) -> Path:
        safe = Path(name).name  # strip any path components
        path = self._artifacts / safe
        path.write_bytes(data)
        return path

    def artifact_path(self, name: str) -> Path:
        return self._artifacts / Path(name).name

    def events(self) -> list[dict]:
        if not self._events_path.exists():
            return []
        return [json.loads(l) for l in self._events_path.read_text().splitlines() if l]

    def manifest(self) -> dict:
        return {
            "events": len(self.events()),
            "artifacts": sorted(p.name for p in self._artifacts.iterdir()),
        }
