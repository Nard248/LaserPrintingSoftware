"""
Design of Experiments (DOE) utilities.

Wraps pyDOE2 to generate full-factorial experiment matrices from
parameter DataFrames, and provides helpers for parameter look-up.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pyDOE2 import fullfact


class ExperimentDesign:
    """Full-factorial experiment design from a parameter table.

    Each column in the parameters DataFrame defines one factor.
    Each row value is one level of that factor.

    Example:
        params = pd.DataFrame({
            "Power (%)": [10, 20, 30],         # 3 levels
            "Speed (mm/s)": [1, 3, 5],         # 3 levels
            "Repetitions": [1, 3, 5],          # 3 levels
        })
        design = ExperimentDesign(params)
        # design.matrix is a DataFrame with 3^3 = 27 rows
    """

    def __init__(self, params: pd.DataFrame):
        """Create a full-factorial design.

        Args:
            params: DataFrame where each column is a factor and rows are levels.
                    All columns must have the same number of rows.
        """
        self.params = params
        self.factors = list(params.columns)
        self.levels = [len(params[col].dropna()) for col in self.factors]

        # Generate the full-factorial index matrix
        indices = fullfact(self.levels).astype(int)

        # Map indices to actual parameter values
        rows = []
        for row_indices in indices:
            row = {}
            for col_idx, factor in enumerate(self.factors):
                row[factor] = params[factor].iloc[row_indices[col_idx]]
            rows.append(row)

        self.matrix = pd.DataFrame(rows)

    @property
    def n_conditions(self) -> int:
        """Total number of experimental conditions."""
        return len(self.matrix)

    def get_condition(self, index: int) -> dict:
        """Get parameter values for a specific condition.

        Args:
            index: Row index in the design matrix.

        Returns:
            Dict mapping factor names to their values for this condition.
        """
        return self.matrix.iloc[index].to_dict()

    def replicate(self, n: int) -> pd.DataFrame:
        """Repeat each condition n times (consecutive replication).

        Args:
            n: Number of replicates per condition.

        Returns:
            DataFrame with n * n_conditions rows.
        """
        return self.matrix.loc[self.matrix.index.repeat(n)].reset_index(drop=True)
