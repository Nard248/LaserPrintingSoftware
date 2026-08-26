"""Layer 0 - raw hardware transports.

These modules are thin wrappers over the underlying HTTP / SPiiPlus APIs.
They contain no policy, no state caching, and no safety logic - they just
expose the primitive calls so that higher layers can compose them.

    laser_http.LaserHttp   - raw HTTP REST endpoints of the Phoebe laser
    stage_tcp.StageTcp     - raw SPiiPlusPython calls over Ethernet TCP

Layer 1 (controllers/) builds primitive actions on top (on/off, move_to, ...).
Layer 2 (controllers/sync.py) coordinates the two devices for printing.

Attributes resolve lazily (PEP 562): StageTcp pulls in the proprietary
SPiiPlusPython wheel only when actually accessed, so this package imports
cleanly on machines without the ACS SDK (dev laptops, CI, simulation mode).
"""

from typing import Any

__all__ = ["LaserHttp", "StageTcp"]


def __getattr__(name: str) -> Any:
    if name == "LaserHttp":
        from laser_printing.hardware.laser_http import LaserHttp
        return LaserHttp
    if name == "StageTcp":
        from laser_printing.hardware.stage_tcp import StageTcp  # needs SPiiPlusPython
        return StageTcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
