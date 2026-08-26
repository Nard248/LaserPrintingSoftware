"""Rig adapters must import and declare capabilities on machines WITHOUT
the ACS vendor wheel; the laser adapter must drive a mocked controller."""

from unittest.mock import MagicMock

import pytest

import labgate.devices.rig as rig
from labgate.errors import DeviceError


def test_rig_module_imports_without_spiiplus():
    # Import already happened at module load; assert the vendor wheel is absent
    # locally, proving the lazy-import design (would have raised otherwise).
    import importlib.util
    assert importlib.util.find_spec("SPiiPlusPython") is None


def test_laser_printing_controllers_import_without_spiiplus(cfg):
    from laser_printing.controllers import LaserController  # lazy __getattr__
    assert LaserController.__name__ == "LaserController"


def test_rig_adapters_declare_capabilities_offline(cfg):
    adapters = [rig.RigStage(cfg), rig.RigLaser(cfg), rig.CameraStub(cfg),
                rig.WhiteLightStub(cfg)]
    for adapter in adapters:
        assert adapter.capabilities(), adapter.device_id
        assert adapter.state().connected is False


def test_rig_laser_drives_controller(cfg):
    adapter = rig.RigLaser(cfg)
    adapter._controller = MagicMock()
    adapter.set_power(30, 2)
    adapter._controller.set_attenuator.assert_called_once_with(30)
    adapter._controller.set_pp_divider.assert_called_once_with(2)
    adapter.on()
    assert adapter.output_on
    adapter.safe_state()
    adapter._controller.off.assert_called()
    assert not adapter.output_on


def test_rig_laser_safe_state_raises_when_off_fails(cfg):
    # Contract (post-review): safe_state must NOT silently report safe when
    # the laser may still be on — it raises and the executor records it.
    adapter = rig.RigLaser(cfg)
    adapter._controller = MagicMock()
    adapter._controller.off.side_effect = RuntimeError("network down")
    with pytest.raises(DeviceError):
        adapter.safe_state()


def test_stubs_refuse_connect(cfg):
    with pytest.raises(DeviceError):
        rig.CameraStub(cfg).connect()
    with pytest.raises(DeviceError):
        rig.WhiteLightStub(cfg).set_on(True)
