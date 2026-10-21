"""阶段二吸附/释放约束测试 (06-09 §2 / §5.8 / §12)."""

import math
from unittest.mock import patch

import pytest

from stage1.material_manager import MaterialManager
from stage1.suction_controller import AttachResult, ReleaseResult
from stage2.suction import (REJECT_CONTACT_GEOMETRY,
                            Stage2SuctionController)

MAT = 200
TCP = 100


class FakeSim:
    handle_world = -1

    def __init__(self, tcp_pose, material_pose):
        self.poses = {TCP: list(tcp_pose), MAT: list(material_pose)}

    def getShapeBB(self, handle):
        return [0.1, 0.1, 0.1]

    def getObjectPose(self, handle, frame=None):
        return list(self.poses[handle])


def controller(tcp_pose, material_pose, manager):
    sim = FakeSim(tcp_pose, material_pose)
    return Stage2SuctionController(
        sim, manager, tcp_handle=TCP, sensor_handle=-1,
        link_handle=-1, release_parent=-1, pad_handle=101)


def manager_with_material():
    manager = MaterialManager()
    manager.register(1, "Part_0001", sim_handle=MAT)
    return manager


MATERIAL = [0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
TCP_OK = [0.0, 0.0, 0.55, 0.0, 0.0, 0.0, 1.0]


def test_valid_contact_evaluation():
    c = controller(TCP_OK, MATERIAL, manager_with_material())
    result = c.evaluate(1)
    assert result is not None
    assert result.valid, result.reasons
    assert result.signed_distance == pytest.approx(0.0, abs=1e-9)


def test_gap_rejected():
    c = controller([0.0, 0.0, 0.57, 0.0, 0.0, 0.0, 1.0], MATERIAL,
                   manager_with_material())
    result = c.evaluate(1)
    assert result is not None and not result.valid


def test_attach_rejects_before_stage1_attach():
    c = controller([0.0, 0.0, 0.57, 0.0, 0.0, 0.0, 1.0], MATERIAL,
                   manager_with_material())
    with patch("stage1.suction_controller.SuctionController.attach",
               side_effect=AssertionError("stage1 attach must not run")):
        result = c.attach(1, 0.0)
    assert not result.ok
    assert result.code == REJECT_CONTACT_GEOMETRY


def test_relative_transform_identity_rotation():
    transform = Stage2SuctionController._relative_transform(
        [0.0, 0.0, 0.55, 0.0, 0.0, 0.0, 1.0],
        [0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0])
    assert transform[:3] == pytest.approx([0.0, 0.0, -0.05])
    assert transform[3:7] == pytest.approx([0.0, 0.0, 0.0, 1.0])


def test_release_rejects_excessive_correction_without_snapping():
    manager = manager_with_material()
    c = controller(TCP_OK, MATERIAL, manager)
    # 实际物料与计划位姿相差 5 cm
    target = [0.05, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
    with patch("stage1.suction_controller.SuctionController.release",
               side_effect=AssertionError("must not release")):
        result = c.release(1, 0.0, target, support_z=0.3)
    assert not result.ok
    assert "RELEASE_CORRECTION_EXCEEDED" in result.message
    assert result.correction_pos == pytest.approx(0.05)


def test_release_uses_actual_pose_keeping_world_pose():
    manager = manager_with_material()
    c = controller(TCP_OK, MATERIAL, manager)
    target = [0.0005, 0.0, 0.5, 0.0, 0.0, 0.0, 1.0]
    seen = {}

    def fake_release(self, uid, sim_time, material_pose, support_z=None):
        seen["pose"] = list(material_pose)
        return ReleaseResult(True, "ok", stable=True)

    with patch("stage1.suction_controller.SuctionController.release",
               fake_release):
        result = c.release(1, 0.0, target, support_z=0.3)
    assert result.ok
    assert seen["pose"] == pytest.approx(MATERIAL)
    assert c.release_pose == pytest.approx(MATERIAL)
