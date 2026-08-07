"""Spec -> canonical toolpath expansion (deterministic, below the boundary).

One shared expansion serves validation (envelope checks), dry-run
(duration + preview rendering), and — for path-shaped ops — execution.
The canonical format matches laser_printing's convention:
path[i] = (point, laser_on_during_segment_arriving_at_i).
"""

from __future__ import annotations

from .geometry import CanonicalPath, GeometryService
from .spec import (
    ExperimentSpec,
    MoveStage,
    PrintStl,
    WriteArray,
    WriteLine,
    WritePowerSweepArray,
    ZStack,
)


def _line_points(start, end, repetitions: int):
    """Reposition to start, then zig-zag exposure segments."""
    out = [(tuple(start), False)]
    a, b = tuple(start), tuple(end)
    for _ in range(repetitions):
        out.append((b, True))
        a, b = b, a
    return out


def op_to_path(op, geometry: GeometryService | None = None) -> CanonicalPath:
    """Expand one operation into path segments (empty for non-motion ops)."""
    if isinstance(op, MoveStage):
        return [(tuple(op.target_mm), False)]
    if isinstance(op, WriteLine):
        return _line_points(op.start_mm, op.end_mm, op.repetitions)
    if isinstance(op, WriteArray):
        path: CanonicalPath = []
        for i in range(op.line_count):
            y = op.y_start_mm + i * op.y_pitch_mm
            path += _line_points((op.x_start_mm, y, op.z_mm),
                                 (op.x_end_mm, y, op.z_mm), op.repetitions)
        return path
    if isinstance(op, WritePowerSweepArray):
        path = []
        for i in range(op.line_count):
            y = op.y_start_mm + i * op.y_pitch_mm
            path += _line_points((op.x_start_mm, y, op.z_mm),
                                 (op.x_end_mm, y, op.z_mm), op.repetitions)
        return path
    if isinstance(op, ZStack):
        path = []
        for power_index in range(len(op.powers())):
            y = op.y_start_mm + power_index * op.y_pitch_mm
            for level in range(op.z_count):
                z = op.z_start_mm + level * op.z_step_mm
                path += _line_points((op.x_start_mm, y, z),
                                     (op.x_end_mm, y, z), op.repetitions)
        return path
    if isinstance(op, PrintStl):
        if geometry is None:
            raise RuntimeError("print_stl expansion requires a GeometryService")
        return geometry.slice_stl(op)
    return []


def spec_to_path(spec: ExperimentSpec,
                 geometry: GeometryService | None = None) -> CanonicalPath:
    """Full spec expansion — the trajectory an approver is signing off on."""
    path: CanonicalPath = []
    for op in spec.operations:
        path += op_to_path(op, geometry)
    return path
