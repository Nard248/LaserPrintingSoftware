"""Run results: telemetry events + binary artifacts (requirement F8)."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

_SAFE_NAME = re.compile(r"^[\w][\w.\- ]{0,120}$")


class UnknownArtifactError(KeyError):
    """Requested artifact name is invalid or absent (maps to HTTP 404)."""


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

    @staticmethod
    def _safe_name(name: str) -> str:
        # Reject separators, '..', and anything not a plain filename; a bad
        # name is a client error (404), never a filesystem escape.
        if "/" in name or "\\" in name or not _SAFE_NAME.match(name) or ".." in name:
            raise UnknownArtifactError(name)
        return name

    def save_artifact(self, name: str, data: bytes) -> Path:
        path = self._artifacts / self._safe_name(name)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic: readers never observe partial files
        return path

    def artifact_path(self, name: str) -> Path:
        return self._artifacts / self._safe_name(name)

    def events(self) -> list[dict]:
        if not self._events_path.exists():
            return []
        return [json.loads(l) for l in self._events_path.read_text().splitlines() if l]

    def manifest(self) -> dict:
        return {
            "events": len(self.events()),
            "artifacts": sorted(
                p.name for p in self._artifacts.iterdir() if not p.name.endswith(".part")
            ),
        }
