"""Model storage and deterministic geometry expansion (slicing).

The trust-boundary rule "the model authors intent, not trajectories" lives
here: an AI submits a `print_stl` op referencing an uploaded model; the
expansion into an actual toolpath happens in THIS module, deterministically,
below the boundary — at validation time (envelope checks), dry-run time
(duration/preview), and execution time. The sliced path is cached
content-addressed, so all three see the identical trajectory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from .errors import ValidationFailed

# Slicing an over-fine mesh can produce paths with millions of points; the
# platform refuses rather than hanging the validator.
MAX_PATH_POINTS = 500_000

CanonicalPath = list[tuple[tuple[float, float, float], bool]]


class ModelInfo(BaseModel):
    model_id: str
    filename: str
    size_bytes: int
    faces: int
    bbox_file_units: dict


class ModelStore:
    """Content-addressed STL storage: model_id is derived from the bytes."""

    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir / "models"
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, filename: str, data: bytes) -> ModelInfo:
        import trimesh

        model_id = "mdl_" + hashlib.sha256(data).hexdigest()[:12]
        stl_path = self._dir / f"{model_id}.stl"
        stl_path.write_bytes(data)
        try:
            mesh = trimesh.load(stl_path, file_type="stl")
            # trimesh parses garbage as an EMPTY mesh rather than raising
            if mesh.is_empty or len(mesh.faces) == 0:
                raise ValidationFailed("STL contains no triangles")
        except ValidationFailed:
            stl_path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            stl_path.unlink(missing_ok=True)
            raise ValidationFailed(f"could not parse STL: {exc}") from exc
        info = ModelInfo(
            model_id=model_id, filename=Path(filename).name, size_bytes=len(data),
            faces=int(len(mesh.faces)),
            bbox_file_units={
                "min": [float(v) for v in mesh.bounds[0]],
                "max": [float(v) for v in mesh.bounds[1]],
            },
        )
        (self._dir / f"{model_id}.json").write_text(info.model_dump_json(indent=2))
        return info

    def info(self, model_id: str) -> ModelInfo:
        meta = self._dir / f"{model_id}.json"
        if not meta.exists():
            raise ValidationFailed(f"unknown model: {model_id}")
        return ModelInfo.model_validate_json(meta.read_text())

    def stl_path(self, model_id: str) -> Path:
        path = self._dir / f"{model_id}.stl"
        if not path.exists():
            raise ValidationFailed(f"unknown model: {model_id}")
        return path

    def list(self) -> list[ModelInfo]:
        return [ModelInfo.model_validate_json(p.read_text())
                for p in sorted(self._dir.glob("*.json"))]


class GeometryService:
    """Slices print_stl ops into canonical paths, with an on-disk cache."""

    def __init__(self, models: ModelStore, storage_dir: Path) -> None:
        self.models = models
        self._cache_dir = storage_dir / "paths"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, op) -> str:
        params = json.dumps({
            "model": op.model_id, "unit": op.unit, "step": op.step_size,
            "start": list(op.start_position_mm), "reps": op.repetition_count,
            "ablation": op.is_ablation,
        }, sort_keys=True)
        return hashlib.sha256(params.encode()).hexdigest()[:16]

    def slice_stl(self, op) -> CanonicalPath:
        """Deterministic slice of a print_stl op; cached by content+params."""
        cache = self._cache_dir / f"{self._cache_key(op)}.json"
        if cache.exists():
            raw = json.loads(cache.read_text())
            return [((p[0][0], p[0][1], p[0][2]), bool(p[1])) for p in raw]

        from laser_printing.path_generation.stl import generate_from_stl

        stl_path = self.models.stl_path(op.model_id)
        path = generate_from_stl(
            stl_file=stl_path, unit=op.unit, step_size=op.step_size,
            start_position=list(op.start_position_mm),
            repetition_count=op.repetition_count,
            is_ablation=op.is_ablation,
        )
        canonical: CanonicalPath = [
            ((float(p[0]), float(p[1]), float(p[2])), bool(laser_on))
            for p, laser_on in path
        ]
        if len(canonical) > MAX_PATH_POINTS:
            raise ValidationFailed(
                f"sliced path has {len(canonical)} points (> {MAX_PATH_POINTS}); "
                "increase step_size")
        cache.write_text(json.dumps(
            [[list(p), on] for p, on in canonical]))
        return canonical
