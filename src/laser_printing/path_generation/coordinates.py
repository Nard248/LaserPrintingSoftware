"""Path generation from explicit coordinates.

Canonical path format (used everywhere in this package)
--------------------------------------------------------
A path is a list of ``(point, laser_on)`` tuples where:

    path[i] = (point_i, laser_during_move_arriving_at_point_i)

So ``path[i].laser`` tells you the laser state **during the segment
from path[i-1] to path[i]**. The very first tuple's laser flag is by
convention ``False`` - there is no "incoming" segment yet, the executor
treats path[0] as an initial reposition.

Example - one print pass (start -> end, laser on):
    [((x_start, y, z), False),     # reposition to start
     ((x_end,   y, z), True)]      # forward sweep with laser on

Example - two back-and-forth print passes:
    [((x_start, y, z), False),
     ((x_end,   y, z), True),      # forward:  laser on
     ((x_start, y, z), True)]      # return:   laser on

Layer 2 `PrintSynchronizer.execute_path` honours this convention.
"""
from __future__ import annotations

from typing import Sequence, Union

Point3D = tuple[float, float, float]
PathSegment = list[tuple[Point3D, bool]]


def single_line(
    x_start: float,
    x_end: float,
    y: float,
    z: float,
    repetitions: int = 1,
) -> PathSegment:
    """One X-line with `repetitions` back-and-forth passes, laser on for all.

    Returns a PathSegment whose first tuple is the reposition-to-start
    (laser off) and every subsequent tuple is a print pass (laser on).

    Args:
        x_start, x_end: X endpoints of the line (mm).
        y, z: fixed Y and Z for the line (mm).
        repetitions: number of sweeps. 1 = single forward pass;
            2 = forward + return; 3 = forward + return + forward; etc.

    Raises:
        ValueError: if repetitions < 1.
    """
    repetitions = int(repetitions)
    if repetitions < 1:
        raise ValueError(f"repetitions must be >= 1, got {repetitions}")

    endpoints: list[Point3D] = [
        (float(x_start), float(y), float(z)),
        (float(x_end), float(y), float(z)),
    ]

    # Reposition to start with laser off.
    path: PathSegment = [(endpoints[0], False)]
    # Each pass alternates destination: end, start, end, start, ...
    # Always laser on - this is the "print" part.
    for i in range(repetitions):
        path.append((endpoints[(i + 1) % 2], True))
    return path


def lines_with_pitch(
    x_start: float,
    x_end: float,
    y_start: float,
    y_pitch: float,
    z_focus: float,
    count: int,
    repetitions: Union[int, Sequence[int]] = 1,
    max_block_size: int | None = None,
) -> list[PathSegment]:
    """Parallel X-lines spaced by `y_pitch` along Y.

    Returns a list of PathSegments (blocks). Each block is independently
    executable; the experiment runner typically calls `execute_path` once
    per block so that laser/motor parameters can change between lines.

    Layout:
        blocks[0]     = single-point "go to first line start" (laser off)
        blocks[1..N]  = one per line (as returned by `single_line`)

    If `max_block_size` is set (> 1), consecutive line blocks are merged
    into groups of at most that many lines, preserving blocks[0] as the
    stand-alone home block.
    """
    if count < 0:
        raise ValueError(f"count must be >= 0, got {count}")
    if isinstance(repetitions, (int, float)):
        reps_list = [int(repetitions)] * count
    else:
        reps_list = [int(r) for r in repetitions]
        if len(reps_list) < count:
            reps_list.extend([1] * (count - len(reps_list)))

    home_point: Point3D = (float(x_start), float(y_start), float(z_focus))
    blocks: list[PathSegment] = [[(home_point, False)]]

    for i in range(count):
        y = y_start + i * y_pitch
        blocks.append(single_line(
            x_start=x_start, x_end=x_end,
            y=y, z=z_focus,
            repetitions=reps_list[i],
        ))

    if max_block_size is not None and max_block_size > 1:
        merged: list[PathSegment] = [blocks[0]]
        current: PathSegment = []
        count_in_block = 0
        for block in blocks[1:]:
            current.extend(block)
            count_in_block += 1
            if count_in_block >= max_block_size:
                merged.append(current)
                current = []
                count_in_block = 0
        if current:
            merged.append(current)
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
    repetitions: Union[int, Sequence[int]] = 1,
) -> tuple[list[PathSegment], list[int]]:
    """Z-stacks at different attenuator powers, one stack per Y position.

    At each attenuator level, print a vertical stack of `z_count` lines at
    the same Y but stepping Z. Between power levels the Y shifts by
    `y_pitch`. Used for 2PP polymerization-threshold finding.

    Returns:
        blocks         - list of PathSegments; blocks[0] is the initial
                         reposition block.
        power_per_line - length matches `blocks[1:]`; the attenuator
                         percentage to use for that line block.
    """
    blocks: list[PathSegment] = [
        [((0.0, 0.0, round(z_focus, 3)), False)],
    ]
    power_per_line: list[int] = []

    powers = list(range(start_power, end_power, power_step))
    for i, power in enumerate(powers):
        current_y = y_start + i * y_pitch
        for j in range(z_count):
            z = z_focus + j * z_step
            rep = (repetitions
                   if isinstance(repetitions, (int, float))
                   else repetitions[j])
            blocks.append(single_line(
                x_start=x_start, x_end=x_end,
                y=current_y, z=round(z, 3),
                repetitions=int(rep),
            ))
            power_per_line.append(int(power))

    return blocks, power_per_line
