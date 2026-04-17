"""Tests for the in-house fullfact replacement."""
import numpy as np
import pandas as pd

from laser_printing.utils.doe import ExperimentDesign, fullfact


def test_fullfact_shape():
    m = fullfact([2, 3])
    assert m.shape == (6, 2)


def test_fullfact_all_combinations():
    m = fullfact([2, 2])
    rows = {tuple(row) for row in m}
    assert rows == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_experiment_design_matrix_size():
    params = pd.DataFrame({
        "A": [1, 2, 3],
        "B": [10, 20, 30],
        "C": [0.1, 0.2, 0.3],
    })
    d = ExperimentDesign(params)
    assert d.n_conditions == 3 * 3 * 3
    for col in params.columns:
        assert set(d.matrix[col].unique()).issubset(set(params[col]))


def test_experiment_design_replicate():
    params = pd.DataFrame({"A": [1, 2], "B": [10, 20]})
    d = ExperimentDesign(params)
    assert d.n_conditions == 4
    rep = d.replicate(3)
    assert len(rep) == 12
