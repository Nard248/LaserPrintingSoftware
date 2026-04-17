"""Layer 0 - raw hardware transports.

These modules are thin wrappers over the underlying HTTP / SPiiPlus APIs.
They contain no policy, no state caching, and no safety logic - they just
expose the primitive calls so that higher layers can compose them.

    laser_http.LaserHttp   - raw HTTP REST endpoints of the Phoebe laser
    stage_tcp.StageTcp     - raw SPiiPlusPython calls over Ethernet TCP

Layer 1 (controllers/) builds primitive actions on top (on/off, move_to, ...).
Layer 2 (controllers/sync.py) coordinates the two devices for printing.
"""

from laser_printing.hardware.laser_http import LaserHttp
from laser_printing.hardware.stage_tcp import StageTcp

__all__ = ["LaserHttp", "StageTcp"]
