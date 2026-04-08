"""
Path generation from explicit coordinates.

Creates motion paths (lists of (point, laser_on) tuples) from coordinate
parameters. Used for line-based experiments where geometry is defined
directly rather than from an STL file.

Key functions:
    - single_line: one horizontal line with optional back-and-forth repetitions
    - lines_with_pitch: parallel lines at regular Y spacing (the workhorse
      for parameter optimization experiments)
    - z_stacks_with_power: vertical stacks of lines for 2PP power sweeps
"""

from __future__ import annotations

from typing import Sequence, Union

# Type alias for a motion path:
#   list of (point, laser_on) tuples where point is (x, y, z)
PathSegment = list[tuple[tuple[float, float, float], bool]]


def single_line(x_start: float, x_end: float, y: float, z: float,
                repetitions: int = 1) -> PathSegment:
    """Generate a single horizontal line with back-and-forth repetitions.

    The line runs along X at fixed Y and Z. With repetitions > 1, the
    stage sweeps back and forth (snake pattern). The laser is ON during
    all sweeps and turns OFF at the end.

    Args:
        x_start: Starting X coordinate.
        x_end: Ending X coordinate.
        y: Fixed Y coordinate.
        z: Fixed Z coordinate.
        repetitions: Number of back-and-forth sweeps (1 = single pass).

    Returns:
        PathSegment: list of ((x, y, z), laser_on) tuples.
    """
    start = (x_start, y, z)
    end = (x_end, y, z)
    endpoints = [start, end]
    repetitions = int(repetitions)

    path: PathSegment = [(start, True)]
    for i in range(repetitions):
        laser_on = i < repetitions - 1  # laser off on the last point
        # Alternate between end and start for snake pattern
        path.append((endpoints[(i + 1) % 2], laser_on))

    return path


def lines_with_pitch(
    x_start: float,
    x_end: float,
    y_start: float,
    y_pitch: float,
    z_focus: float,
    count: int,
    repetitions: Union[int, list[int]] = 1,
    max_block_size: int | None = None,
) -> list[PathSegment]:
    """Generate parallel horizontal lines at regular Y spacing.

    This is the reconstructed function that was missing from the original
    codebase but used in all parameter optimization experiments. Each line
    is a horizontal sweep along X at a fixed Y and Z, with lines advancing
    in the Y direction by y_pitch.

    Lines are grouped into "blocks" — each block is an independent execution
    unit that the experiment runner feeds to the motor controller one at a time.
    This allows changing laser/motor parameters between blocks.

    The first block is always a "home" move: reposition to the start of the
    first line with laser off. Subsequent blocks each contain one line's
    worth of motion (including the reposition move from the previous line).

    Args:
        x_start: Starting X coordinate for each line.
        x_end: Ending X coordinate for each line.
        y_start: Y coordinate of the first line.
        y_pitch: Y spacing between consecutive lines (mm).
        z_focus: Fixed Z coordinate (focal plane height).
        count: Number of lines to generate.
        repetitions: Back-and-forth sweeps per line. Either a single int
                     (same for all lines) or a list of ints (one per line).
        max_block_size: If set, split large groups of lines into sub-blocks
                        of at most this many lines each.

    Returns:
        List of PathSegments. blocks[0] is the initial positioning move.
        blocks[1:] each contain a line's motion data.
    """
    if isinstance(repetitions, (int, float)):
        reps = [int(repetitions)] * count
    else:
        reps = [int(r) for r in repetitions]
        if len(reps) < count:
            reps.extend([1] * (count - len(reps)))

    # Block 0: initial positioning move (laser off)
    home_point = (x_start, y_start, z_focus)
    blocks: list[PathSegment] = [[(home_point, False)]]

    for i in range(count):
        y = y_start + i * y_pitch
        line = single_line(
            x_start=x_start, x_end=x_end,
            y=y, z=z_focus, repetitions=reps[i]
        )
        blocks.append(line)

    # If max_block_size is set, merge consecutive blocks into larger blocks
    if max_block_size is not None and max_block_size > 1:
        merged: list[PathSegment] = [blocks[0]]  # keep home block separate
        current_block: PathSegment = []
        lines_in_block = 0
        for block in blocks[1:]:
            current_block.extend(block)
            lines_in_block += 1
            if lines_in_block >= max_block_size:
                merged.append(current_block)
                current_block = []
                lines_in_block = 0
        if current_block:
            merged.append(current_block)
        return merged

    return blocks


def z_stacks_with_power(
    x_start: float,
    x_end: float,
    y_start: float,
    y_pitch: float,
    z_focus: float,
    z_step: float,
    z_count: int,
    start_power: int,
    end_power: int,
    power_step: int,
    repetitions: Union[int, list[int]] = 1,
) -> tuple[list[PathSegment], list[int]]:
    """Generate Z-stacks of lines at different power levels for 2PP experiments.

    At each power level, a vertical stack of z_count lines is printed at
    the same Y position but incrementing Z. The Y position shifts by y_pitch
    between power levels.

    This is the key pattern for two-photon polymerization (2PP) parameter
    optimization: varying both the Z-depth and laser power to find the
    polymerization threshold.

    Args:
        x_start, x_end: X-axis endpoints for each line.
        y_start: Y coordinate of the first stack.
        y_pitch: Y spacing between stacks (one per power level).
        z_focus: Base Z coordinate (focal plane).
        z_step: Z spacing between lines within a stack.
        z_count: Number of lines per stack.
        start_power: Starting attenuator percentage.
        end_power: Ending attenuator percentage (exclusive).
        power_step: Attenuator percentage increment between stacks.
        repetitions: Sweeps per line (int or list per z-level).

    Returns:
        Tuple of (blocks, power_per_line):
            blocks: list of PathSegments (blocks[0] is initial positioning).
            power_per_line: list of attenuator % values, one per line block.
    """
    # Initial positioning block
    blocks: list[PathSegment] = [
        [((0.0, 0.0, round(z_focus, 3)), False)]
    ]
    power_per_line: list[int] = []

    powers = list(range(start_power, end_power, power_step))

    for i, power in enumerate(powers):
        current_y = y_start + i * y_pitch

        for j in range(z_count):
            z = z_focus + j * z_step
            rep = repetitions if isinstance(repetitions, int) else repetitions[j]
            line = single_line(
                x_start=x_start, x_end=x_end,
                y=current_y, z=round(z, 3), repetitions=rep
            )
            blocks.append(line)
            power_per_line.append(power)

    return blocks, power_per_line
