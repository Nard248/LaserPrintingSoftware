"""
Visualization utilities for motion paths and experiment results.

Provides 3D path plotting (to verify scan patterns before printing)
and motion profile plots (velocity, acceleration, jerk vs time/position).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import numpy as np


def plot_path_3d(
    path: list[tuple[Sequence[float], bool]],
    title: str = "Print Path",
    show: bool = True,
    save_path: str | Path | None = None,
) -> None:
    """Plot a motion path in 3D, coloring print vs reposition segments.

    Print segments (laser ON) are shown in blue; reposition segments
    (laser OFF) are shown in light gray dashed lines.

    Args:
        path: List of (point, laser_on) tuples.
        title: Plot title.
        show: Whether to display the plot interactively.
        save_path: If set, save the figure to this file path.
    """
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")

    for i in range(len(path) - 1):
        (x1, y1, z1), laser_on = path[i]
        (x2, y2, z2), _ = path[i + 1]

        if laser_on:
            ax.plot([x1, x2], [y1, y2], [z1, z2],
                    color="blue", linewidth=1.5, alpha=0.9)
        else:
            ax.plot([x1, x2], [y1, y2], [z1, z2],
                    color="lightgray", linewidth=0.5, linestyle="--", alpha=0.5)

    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    ax.set_zlabel("Z (mm)")
    ax.set_title(title)

    if save_path:
        plt.savefig(str(save_path), dpi=200, bbox_inches="tight")
    if show:
        plt.show()
    plt.close()


def plot_motion_profile(
    time_s: np.ndarray,
    velocity: np.ndarray,
    target_velocity: float,
    tolerance_fraction: float = 0.05,
    title: str = "Motion Profile",
    show: bool = True,
    save_path: str | Path | None = None,
) -> None:
    """Plot velocity vs time with target band overlay.

    Args:
        time_s: Time array in seconds.
        velocity: Velocity array in mm/s.
        target_velocity: Target velocity in mm/s.
        tolerance_fraction: Tolerance band (e.g. 0.05 for +/-5%).
        title: Plot title.
        show: Whether to display interactively.
        save_path: If set, save figure.
    """
    speed = np.abs(velocity)
    lower = target_velocity * (1 - tolerance_fraction)
    upper = target_velocity * (1 + tolerance_fraction)

    fig, axes = plt.subplots(1, 1, figsize=(10, 5))

    axes.plot(time_s, speed, label="Speed |V|", color="blue", linewidth=1)
    axes.axhline(target_velocity, color="green", linestyle="--",
                 label=f"Target: {target_velocity} mm/s")
    axes.axhline(lower, color="orange", linestyle=":", alpha=0.7,
                 label=f"±{tolerance_fraction*100:.0f}% band")
    axes.axhline(upper, color="orange", linestyle=":", alpha=0.7)

    # Shade the tolerance band
    axes.axhspan(lower, upper, alpha=0.1, color="green")

    axes.set_xlabel("Time (s)")
    axes.set_ylabel("Speed (mm/s)")
    axes.set_title(title)
    axes.legend()
    axes.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(str(save_path), dpi=200, bbox_inches="tight")
    if show:
        plt.show()
    plt.close()
