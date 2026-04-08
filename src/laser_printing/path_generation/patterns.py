"""
Common printing patterns used across experiments.

Provides reusable building blocks like indicator lines (to mark experiment
boundaries on the substrate) and grid patterns.
"""

from __future__ import annotations

from laser_printing.path_generation.coordinates import PathSegment, lines_with_pitch


def indicator_lines(
    x_start: float,
    x_end: float,
    y_center: float,
    z_focus: float,
    count: int = 2,
    pitch: float = 0.04,
) -> list[PathSegment]:
    """Generate closely-spaced indicator lines to mark experiment boundaries.

    These are thin, closely-spaced lines that are visible under a microscope
    and serve as visual markers for where an experiment starts or ends on
    the substrate.

    Args:
        x_start: Start X coordinate.
        x_end: End X coordinate.
        y_center: Y coordinate for the center of the indicator group.
        z_focus: Z coordinate (focal plane).
        count: Number of indicator lines (default: 2 for start, 3 for end).
        pitch: Y spacing between indicator lines (default: 40 microns).

    Returns:
        List of PathSegments (blocks) for the indicator lines.
    """
    # Center the indicator lines around y_center
    y_start = y_center - (count - 1) * pitch / 2

    return lines_with_pitch(
        x_start=x_start, x_end=x_end,
        y_start=y_start, y_pitch=pitch,
        z_focus=z_focus, count=count,
        repetitions=1,
    )
