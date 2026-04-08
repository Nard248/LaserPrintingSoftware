"""
Mesh manipulation utilities for STL-based path generation.

Handles scaling (mm/micron/nm), translation to physical coordinates,
and surface discretization for laser scan path computation.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import trimesh

# Scale factors: all internal coordinates are in mm.
# STL files may be authored in microns or nanometers.
SCALE_FACTORS = {
    "mm": 1.0,
    "micron": 1e-3,       # 1 micron = 0.001 mm
    "nm": 1e-6,           # 1 nm = 0.000001 mm
}


def to_mm(value: float, unit: str) -> float:
    """Convert a value from the given unit to millimeters.

    Args:
        value: The numeric value to convert.
        unit: One of "mm", "micron", "nm".

    Returns:
        Value in millimeters.
    """
    if unit not in SCALE_FACTORS:
        raise ValueError(
            f"Unknown unit '{unit}'. Must be one of {list(SCALE_FACTORS.keys())}"
        )
    return value * SCALE_FACTORS[unit]


def scale_mesh(mesh: Union[trimesh.Trimesh, trimesh.Geometry],
               unit: str) -> Union[trimesh.Trimesh, trimesh.Geometry]:
    """Scale mesh vertices from the given unit to millimeters.

    Args:
        mesh: Trimesh object whose vertices are in the given unit.
        unit: The unit the mesh was authored in ("mm", "micron", "nm").

    Returns:
        The same mesh object with vertices scaled to mm (modified in-place).
    """
    factor = SCALE_FACTORS.get(unit)
    if factor is None:
        raise ValueError(
            f"Unknown unit '{unit}'. Must be one of {list(SCALE_FACTORS.keys())}"
        )
    mesh.vertices *= factor
    return mesh


def translate_mesh(mesh: Union[trimesh.Trimesh, trimesh.Geometry],
                   center_position: np.ndarray,
                   auto_z: bool = True,
                   is_ablation: bool = False) -> Union[trimesh.Trimesh, trimesh.Geometry]:
    """Translate mesh to physical coordinates centered at the given position.

    For fabrication (2PP): the mesh bottom surface is placed at center_position.z
    so that printing builds upward from the focal plane.

    For ablation: the mesh top surface is placed at center_position.z
    so that material is removed downward from the surface.

    Args:
        mesh: Trimesh object with vertices already in mm.
        center_position: [x, y, z] where the object center should be placed.
        auto_z: If True, automatically align the mesh bottom (fabrication)
                or top (ablation) to center_position.z.
        is_ablation: If True, align top surface; if False, align bottom surface.

    Returns:
        The mesh with vertices translated to physical coordinates.
    """
    offset = np.array(center_position, dtype=float).copy()
    if auto_z:
        if is_ablation:
            # Align mesh top to the focal plane (ablation removes downward)
            z_offset = mesh.vertices[:, 2].max()
        else:
            # Align mesh bottom to the focal plane (fabrication builds upward)
            z_offset = mesh.vertices[:, 2].min()
        offset[2] -= z_offset
    mesh.vertices += offset
    return mesh


def discretize_line(start: np.ndarray, end: np.ndarray,
                    step_size: float) -> np.ndarray:
    """Sample points along a line at regular intervals.

    Uses step_size/sqrt(2) as the actual interval to ensure that
    diagonal lines have sufficient point density (the sqrt(2) factor
    accounts for the worst-case 45-degree diagonal in 2D).

    Args:
        start: Start point [x, y, z].
        end: End point [x, y, z].
        step_size: Desired spacing between points.

    Returns:
        Array of shape (N, 3) with sampled points (excludes start, includes end).
    """
    effective_step = step_size / np.sqrt(2)
    length = float(np.linalg.norm(np.array(end) - np.array(start)))
    if length < 1e-10:
        return np.array([end])
    n_points = int(length // effective_step) + 1
    return np.linspace(start, end, max(n_points, 2))
