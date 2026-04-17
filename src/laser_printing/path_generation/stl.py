"""
STL-based path generation for 3D laser printing and ablation.

Converts an STL mesh file into a sequence of horizontal scan lines that
the laser traces layer-by-layer. The pipeline:

    1. Load STL and scale/translate to physical coordinates
    2. Sample the mesh surface into a dense point cloud
    3. Bin points into Z-layers, then Y-rows within each layer
    4. Extract horizontal scan lines (min-X to max-X per Y-row)
    5. Optimize into a snake pattern (alternating left-right direction)
    6. Label each segment as "print" (laser on) or "reposition" (laser off)

For fabrication (2PP): layers are processed bottom-up.
For ablation: layers are processed top-down.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Union

import numpy as np
from tqdm import tqdm

from laser_printing.utils.mesh import (
    discretize_line,
    scale_mesh,
    to_mm,
    translate_mesh,
)

# Type alias
Point3D = tuple[float, float, float]
PathWithLaser = list[tuple[Point3D, bool]]


def generate_from_stl(
    stl_file: Union[str, Path],
    unit: str,
    step_size: float,
    start_position: np.ndarray | list[float],
    repetition_count: int = 1,
    auto_z: bool = True,
    is_ablation: bool = False,
    discretization_type: str = "right_short",
) -> PathWithLaser:
    """Main entry point: convert an STL file to a laser print path.

    Args:
        stl_file: Path to the .stl file.
        unit: Unit the STL file is authored in ("mm", "micron", "nm").
        step_size: Discretization step size in the given unit.
        start_position: [x, y, z] physical coordinates for the object center.
        repetition_count: Number of back-and-forth sweeps per scan line.
        auto_z: Automatically align mesh bottom/top to start_position.z.
        is_ablation: If True, process layers top-down (ablation mode).
        discretization_type: Binning strategy ("right_short", "right_long",
                             "short", "long").

    Returns:
        List of (point, laser_on) tuples defining the complete print path.
    """
    import trimesh

    start_position = np.array(start_position, dtype=float)
    mesh = trimesh.load_mesh(str(stl_file))
    mesh = scale_mesh(mesh, unit)
    mesh = translate_mesh(mesh, start_position, auto_z=auto_z, is_ablation=is_ablation)
    step_mm = to_mm(step_size, unit)

    point_cloud = _mesh_to_point_cloud(mesh, step_mm)
    layers, z_bins, y_bins = _bin_by_z_y(
        point_cloud, step_mm, discretization_type, is_ablation
    )
    scan_lines = _extract_scan_lines(layers, z_bins, y_bins)
    ordered_points = _snake_order(scan_lines, repetition_count, start_position)
    path = _assign_laser_states(ordered_points)
    return path


# -- Pipeline stages -------------------------------------------------------

def _mesh_to_point_cloud(mesh, step_size: float) -> np.ndarray:
    """Sample the mesh surface into a dense point cloud.

    Walks every triangular face, discretizes its edges, then fills in the
    interior by connecting the apex to points along the opposite edge.
    """
    faces = mesh.faces
    all_points = np.zeros((0, 3))

    for face in tqdm(faces, desc="Mesh → point cloud"):
        v0, v1, v2 = mesh.vertices[face]
        face_points = np.array([v0, v1, v2])

        # Discretize the base edge (v1 → v2)
        edge_points = discretize_line(v1, v2, step_size)
        targets = np.vstack([np.array([v1, v2]), edge_points])

        # Connect apex (v0) to each point on the base edge
        for target in targets:
            interior = discretize_line(v0, target, step_size)
            face_points = np.vstack([face_points, interior])

        all_points = np.vstack([all_points, face_points])

    return np.unique(all_points, axis=0)


def _bin_by_z_y(
    point_cloud: np.ndarray,
    step_size: float,
    discretization_type: str,
    is_ablation: bool,
) -> tuple[dict, np.ndarray, np.ndarray]:
    """Bin the point cloud into Z-layers and Y-rows.

    Returns:
        (layers, z_bins, y_bins) where layers is a nested dict:
        layers[z_value][y_value] = array of points in that bin.
    """
    min_pt = point_cloud.min(axis=0)
    max_pt = point_cloud.max(axis=0)

    # Extension controls whether bin edges align to mesh min/max
    extensions = {
        "right_short": (0, step_size / 1000),
        "right_long":  (0, step_size),
        "short":       (step_size / 2, -step_size / 2),
        "long":        (-step_size / 2, step_size / 2),
    }
    if discretization_type not in extensions:
        raise ValueError(
            f"Unknown discretization_type '{discretization_type}'. "
            f"Valid: {list(extensions.keys())}"
        )
    min_ext, max_ext = extensions[discretization_type]

    # Z partition (reversed for ablation: top-down)
    if is_ablation:
        z_bins = np.arange(max_pt[2] + min_ext, min_pt[2] + max_ext, -step_size)
    else:
        z_bins = np.arange(min_pt[2] + min_ext, max_pt[2] + max_ext, step_size)

    y_bins = np.arange(min_pt[1] + min_ext, max_pt[1] + max_ext, step_size)

    # Digitize points into bins
    z_indices = np.digitize(point_cloud[:, 2], z_bins, right=True) - 1
    y_indices = np.digitize(point_cloud[:, 1], y_bins, right=True) - 1

    # Build nested dict: layers[z][y] = points
    layers: dict[float, dict[float, np.ndarray]] = defaultdict(
        lambda: defaultdict(list)
    )
    for z_idx, y_idx, point in zip(z_indices, y_indices, point_cloud):
        if 0 <= z_idx < len(z_bins) and 0 <= y_idx < len(y_bins):
            layers[z_bins[z_idx]][y_bins[y_idx]].append(point)

    # Convert lists to arrays and remove empty bins
    result = {}
    for z, y_dict in layers.items():
        y_result = {}
        for y, points in y_dict.items():
            if len(points) > 0:
                y_result[y] = np.array(points)
        if y_result:
            result[z] = y_result

    return result, z_bins, y_bins


def _extract_scan_lines(
    layers: dict, z_bins: np.ndarray, y_bins: np.ndarray
) -> list[np.ndarray]:
    """Extract horizontal scan lines (min-X to max-X) from binned points.

    Each scan line is a 2-point array: [[x_min, y, z], [x_max, y, z]].
    """
    lines = []
    for z_val in z_bins:
        if z_val not in layers:
            continue
        for y_val in y_bins:
            if y_val not in layers[z_val]:
                continue
            points = layers[z_val][y_val]
            x_coords = points[:, 0]
            start = np.array([float(x_coords.min()), float(y_val), float(z_val)])
            end = np.array([float(x_coords.max()), float(y_val), float(z_val)])
            lines.append(np.array([start, end]))
    return lines


def _snake_order(
    scan_lines: list[np.ndarray],
    repetition_count: int,
    start_position: np.ndarray,
) -> list[np.ndarray]:
    """Reorder scan lines into a snake pattern and add repetitions.

    Snake pattern alternates direction: line 1 left→right, line 2 right→left,
    etc. This minimizes repositioning distance between consecutive lines.

    Also repeats each line repetition_count times (for multi-pass exposure)
    and adds the start/end connection points.
    """
    # Repeat each line
    repeated = []
    for line in scan_lines:
        for _ in range(repetition_count):
            repeated.append(line)

    # Build snake-ordered point list
    points = []
    direction = 1  # 1 = forward (start→end), -1 = reverse (end→start)
    for line in repeated:
        if direction == 1:
            points.append(line[0])  # start point
            points.append(line[1])  # end point
        else:
            points.append(line[1])  # end point first
            points.append(line[0])  # start point second
        direction *= -1

    # Remove consecutive duplicate points
    cleaned = [start_position.copy()]
    for pt in points:
        if not np.array_equal(pt, cleaned[-1]):
            cleaned.append(pt)
    cleaned.append(start_position.copy())

    return cleaned


def _assign_laser_states(ordered_points: list[np.ndarray]) -> PathWithLaser:
    """Assign laser state to each segment using the canonical end-of-segment
    convention: ``path[i].laser`` = laser state during the move FROM
    ``ordered_points[i-1]`` TO ``ordered_points[i]``.

    The rule used here: laser is ON when the segment moves purely along X
    (Y and Z unchanged), otherwise OFF (repositioning between scan lines).

    `path[0].laser` is always False by convention (the executor treats
    path[0] as an initial reposition and ignores the flag).
    """
    path: PathWithLaser = []
    # path[0]: initial reposition point, laser off.
    p0 = ordered_points[0]
    path.append(((float(p0[0]), float(p0[1]), float(p0[2])), False))

    # path[i] for i>=1: state during segment i-1 -> i.
    for i in range(1, len(ordered_points)):
        prev = ordered_points[i - 1]
        curr = ordered_points[i]
        same_y = abs(prev[1] - curr[1]) < 1e-6
        same_z = abs(prev[2] - curr[2]) < 1e-6
        laser_on = bool(same_y and same_z)
        path.append(((float(curr[0]), float(curr[1]), float(curr[2])), laser_on))

    return path
