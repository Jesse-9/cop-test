"""传送带 pad 闭环采集与推进的纯单元测试。"""

import pytest

from reference.factory_physics import advance_belt_pad_loop, capture_belt_pad_loop


class FakeBeltSim:
    object_shape_type = 0
    handle_all = -2
    handle_parent = -11

    def __init__(self):
        self.pads = [10, 11, 12, 13]
        self.poses = {
            10: [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            11: [1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
            12: [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            13: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        }
        self.updated = {}

    def getObjectsInTree(self, _root, _object_type, _options):
        return self.pads

    def getObjectType(self, _handle):
        return self.object_shape_type

    def getObjectAlias(self, _handle):
        return "pad"

    def getObjectPose(self, handle, _relative_to):
        return self.poses[handle]

    def interpolatePoses(self, pose1, pose2, fraction):
        return [
            a + (b - a) * fraction for a, b in zip(pose1, pose2)
        ]

    def setObjectPose(self, handle, pose, _relative_to):
        self.updated[handle] = pose


def test_belt_pads_follow_the_closed_loop_instead_of_local_x_only():
    sim = FakeBeltSim()
    loop = capture_belt_pad_loop(sim, 1)

    assert loop is not None
    assert loop["spacing"] == pytest.approx(1.0)
    assert advance_belt_pad_loop(sim, loop, 0.5)
    assert sim.updated[10][:3] == pytest.approx([0.5, 0.0, 1.0])
    assert sim.updated[11][:3] == pytest.approx([1.0, 0.0, 0.5])


def test_belt_phase_wraps_without_changing_pad_spacing():
    sim = FakeBeltSim()
    loop = capture_belt_pad_loop(sim, 1)

    assert advance_belt_pad_loop(sim, loop, 4.0)
    assert loop["phase"] == pytest.approx(0.0)
    for handle in sim.pads:
        assert sim.updated[handle] == pytest.approx(sim.poses[handle])
