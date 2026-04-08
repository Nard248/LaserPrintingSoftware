"""
I/O utilities for saving and loading experiment data.

Handles JSON serialization of motion paths (with numpy array conversion),
experiment metadata, and CSV results.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import numpy as np


def save_path_data(path_data: list, filepath: Path | str) -> None:
    """Save a motion path (list of (point, laser_state) tuples) to JSON.

    Handles numpy array conversion automatically.

    Args:
        path_data: List of (point, laser_on) tuples where point is [x, y, z].
        filepath: Output JSON file path.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    serializable = _convert_for_json(path_data)
    with open(filepath, "w") as f:
        json.dump(serializable, f, indent=4)


def load_path_data(filepath: Path | str) -> list:
    """Load a motion path from a JSON file.

    Args:
        filepath: Path to the JSON file.

    Returns:
        List of (point, laser_state) tuples.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Path data file not found: {filepath}")
    with open(filepath) as f:
        return json.load(f)


def save_experiment(experiment_dir: Path | str,
                    path_data: list,
                    description: str = "",
                    stl_file: Path | str | None = None) -> Path:
    """Save a complete experiment to a structured directory.

    Creates:
        <experiment_dir>/
            description.txt
            lines_and_laser_states.json
            STL/           (if stl_file provided)
            LOGS/
            Visualization/

    Args:
        experiment_dir: Directory to create/write into.
        path_data: Motion path data.
        description: Text description of the experiment.
        stl_file: Optional STL file to copy into the experiment directory.

    Returns:
        Path to the experiment directory.
    """
    experiment_dir = Path(experiment_dir)
    for subdir in ["STL", "LOGS", "Visualization"]:
        (experiment_dir / subdir).mkdir(parents=True, exist_ok=True)

    save_path_data(path_data, experiment_dir / "lines_and_laser_states.json")

    with open(experiment_dir / "description.txt", "w") as f:
        f.write(description)

    if stl_file is not None:
        shutil.copy(str(stl_file), experiment_dir / "STL")

    return experiment_dir


def _convert_for_json(obj: Any) -> Any:
    """Recursively convert numpy types to Python native types for JSON."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _convert_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        converted = [_convert_for_json(item) for item in obj]
        return type(obj)(converted) if isinstance(obj, tuple) else converted
    return obj
