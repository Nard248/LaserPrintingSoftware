"""Toolpath preview rendering for the approval step.

The proposal (§8, §10) requires the approver to give informed consent, not
a reflexive click — so the dry-run renders the ACTUAL expanded trajectory
(the same one validation checked) to an image the approver and the chat UI
can look at before signing off.
"""

from __future__ import annotations

from pathlib import Path

from .geometry import GeometryService
from .spec import ExperimentSpec

PREVIEW_ARTIFACT = "dryrun_preview.png"


def render_preview(spec: ExperimentSpec, geometry: GeometryService | None,
                   out_path: Path) -> bool:
    """Render the spec's full toolpath to a PNG. Returns False if the spec
    has no motion to draw. Uses a headless matplotlib backend."""
    from .pathing import spec_to_path

    path = spec_to_path(spec, geometry)
    if not path:
        return False

    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    from laser_printing.visualization.plotting import plot_path_3d

    try:
        plot_path_3d(path, title=spec.title, show=False, save_path=out_path)
    finally:
        plt.close("all")  # never accumulate figures in a long-running server
    return True
