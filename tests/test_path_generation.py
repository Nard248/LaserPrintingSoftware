"""Tests for path generation conventions.

Locks in the end-of-segment convention and the reps>=1 fix.
Run with:  .venv/Scripts/python -m pytest tests/test_path_generation.py -v
"""
from __future__ import annotations

import numpy as np

from laser_printing.path_generation.coordinates import (
    lines_with_pitch,
    single_line,
    z_stacks_with_power,
)
from laser_printing.path_generation.stl import _assign_laser_states


def print_passes(path):
    """Count (non-repositioning) print segments in a path."""
    return sum(1 for (_, laser_on) in path[1:] if laser_on)


# ---- single_line ---------------------------------------------------------

def test_single_line_reps_1_has_one_print_pass():
    p = single_line(0, 1, 0, 0, repetitions=1)
    assert p[0][1] is False, "first tuple must be reposition (laser off)"
    assert print_passes(p) == 1


def test_single_line_reps_2_has_two_print_passes():
    p = single_line(0, 1, 0, 0, repetitions=2)
    assert print_passes(p) == 2
    # snake: start -> end -> start
    pts = [pt for pt, _ in p]
    assert pts == [(0, 0, 0), (1, 0, 0), (0, 0, 0)]


def test_single_line_reps_n_has_n_print_passes():
    for n in range(1, 8):
        assert print_passes(single_line(0, 1, 0, 0, repetitions=n)) == n


def test_single_line_invalid_reps():
    import pytest
    with pytest.raises(ValueError):
        single_line(0, 1, 0, 0, repetitions=0)


# ---- lines_with_pitch ----------------------------------------------------

def test_lines_with_pitch_block_count():
    blocks = lines_with_pitch(0, 1, 0, 0.1, 6.0, count=3, repetitions=1)
    assert len(blocks) == 1 + 3, "expect home block + one per line"
    # Home block is single-point, laser off.
    assert blocks[0] == [((0.0, 0.0, 6.0), False)]
    # Each line block has one print pass.
    for b in blocks[1:]:
        assert print_passes(b) == 1


def test_lines_with_pitch_y_advances_correctly():
    blocks = lines_with_pitch(0, 1, 0, 0.1, 6.0, count=3, repetitions=1)
    ys = [b[-1][0][1] for b in blocks[1:]]  # y of final point
    assert ys == [0.0, 0.1, 0.2]


def test_lines_with_pitch_per_line_repetitions():
    blocks = lines_with_pitch(0, 1, 0, 0.1, 6.0, count=3,
                              repetitions=[1, 2, 3])
    assert [print_passes(b) for b in blocks[1:]] == [1, 2, 3]


def test_lines_with_pitch_max_block_size_merges():
    blocks = lines_with_pitch(0, 1, 0, 0.1, 6.0, count=5,
                              repetitions=1, max_block_size=2)
    # Home + ceil(5/2) = 3 merged groups
    assert len(blocks) == 1 + 3


# ---- z_stacks_with_power -------------------------------------------------

def test_z_stacks_power_alignment():
    blocks, powers = z_stacks_with_power(
        x_start=0, x_end=1, y_start=0, y_pitch=0.05,
        z_focus=6, z_step=0.001, z_count=3,
        start_power=10, end_power=16, power_step=2,
        repetitions=1,
    )
    # 3 power levels (10, 12, 14) * 3 z-lines = 9 line blocks + 1 home.
    assert len(blocks) == 10
    assert len(powers) == 9
    assert powers == [10, 10, 10, 12, 12, 12, 14, 14, 14]


# ---- STL laser state assignment -----------------------------------------

def test_stl_assign_laser_states_convention():
    # Two X-scans at the same Y,Z joined by a Y-reposition.
    pts = [np.array([0, 0, 6]),   # start
           np.array([1, 0, 6]),   # print forward (same Y,Z) -> laser on
           np.array([1, 0.1, 6]), # reposition (Y changes) -> laser off
           np.array([0, 0.1, 6])] # print (same Y,Z) -> laser on
    path = _assign_laser_states(pts)
    assert len(path) == 4
    assert path[0][1] is False            # initial reposition
    assert path[1][1] is True             # x-scan
    assert path[2][1] is False            # y-reposition
    assert path[3][1] is True             # x-scan
