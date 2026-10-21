"""suction.py — 阶段二吸附/释放：外表面接触判据与无瞬移释放 (06-09 §2, §5.8).

复用 :class:`stage1.suction_controller.SuctionController` 的 UID/传感器/
速度/属性快照逻辑, 在两侧增加阶段二约束:

  - ``attach``: 先用物料局部包围盒计算带符号平面间隙、面内偏移、杯口覆盖
    与工具法向夹角; 任一不满足即拒绝, 不进入阶段一吸附;
  - ``release``: 物料仍挂在 TCP 上时读取**真实世界位姿**, 只有它与计划
    放置位姿的修正量在冻结误差内才允许释放; 释放时传入真实位姿, 避免阶段一
    ``setObjectPose`` 把错误路径瞬移修正。

逻辑吸附不模拟真空压力/流量/负载极限, 不得表述为物理吸力。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from stage1.suction_controller import (ATTACH_OK, AttachResult,
                                       ReleaseResult, SuctionController)

from . import config as cfg
from .contact import (ContactEvaluation, ContactLimits, LocalBox,
                      evaluate_contact, quat_angle_between, quat_conjugate,
                      quat_multiply, quat_rotate)

REJECT_CONTACT_GEOMETRY = "REJECT_CONTACT_GEOMETRY"
REJECT_RELEASE_CORRECTION = "REJECT_RELEASE_CORRECTION"


@dataclass
class Stage2AttachResult(AttachResult):
    contact: Optional[ContactEvaluation] = None
    transform: Optional[list[float]] = None

@dataclass
class Stage2ReleaseResult(ReleaseResult):
    correction_pos: float = 0.0
    correction_ang: float = 0.0
    release_pose: Optional[list[float]] = None


class Stage2SuctionController(SuctionController):
    def __init__(self, sim, manager, tcp_handle: int, sensor_handle: int,
                 link_handle: int = -1, release_parent: int = -1,
                 logger=None, pad_handle: int = -1,
                 limits: Optional[ContactLimits] = None,
                 cup_radius: float = cfg.CUP_RADIUS_DESIGN) -> None:
        super().__init__(sim, manager, tcp_handle, sensor_handle,
                         link_handle=link_handle,
                         release_parent=release_parent, logger=logger,
                         pad_handle=pad_handle)
        self.limits = limits or ContactLimits(
            max_gap=cfg.CONTACT_MAX_GAP,
            min_signed_distance=cfg.CONTACT_MIN_SIGNED_DISTANCE,
            max_lateral=cfg.CONTACT_MAX_LATERAL,
            max_normal_angle=cfg.CONTACT_MAX_NORMAL_ANGLE,
            face_tolerance=cfg.CONTACT_FACE_TOLERANCE,
            require_cup_coverage=cfg.CONTACT_REQUIRE_CUP_COVERAGE,
        )
        self.cup_radius = float(cup_radius)
        self.last_contact: Optional[ContactEvaluation] = None
        self.attach_transform: Optional[list[float]] = None
        self.release_pose: Optional[list[float]] = None
        self.release_target: Optional[list[float]] = None

    # ── 几何读数 ────────────────────────────────────────────

    def material_box(self, handle: int) -> LocalBox:
        """物料局部包围盒; 当前物料是 createPrimitiveShape 长方体.

        ``getShapeBB`` 返回对象局部尺寸; 对内置 primitive 形状几何中心与
        对象原点重合, 因此用对称 min/max。该假设写入证据, 不用于任意网格。
        """
        value = self.sim.getShapeBB(handle)
        size = (value[0] if isinstance(value, (tuple, list))
                and len(value) == 2 else value)
        return LocalBox.from_size(size)

    def material_pose(self, handle: int) -> list[float]:
        return list(self.sim.getObjectPose(handle, self.sim.handle_world))

    def evaluate(self, uid: int) -> Optional[ContactEvaluation]:
        rec = self.manager.by_uid(uid)
        if rec is None or rec.sim_handle is None:
            return None
        pose = self.material_pose(rec.sim_handle)
        tcp = list(self.sim.getObjectPose(self.tcp_handle,
                                          self.sim.handle_world))
        return evaluate_contact(tcp, pose, self.material_box(rec.sim_handle),
                                self.limits, self.cup_radius, uid=uid)

    # ── 吸附 ────────────────────────────────────────────────

    def attach(self, target_uid: int, sim_time: float) -> AttachResult:
        try:
            evaluation = self.evaluate(target_uid)
        except Exception as exc:
            return Stage2AttachResult(
                REJECT_CONTACT_GEOMETRY,
                f"接触几何读取失败: {type(exc).__name__}: {exc}")
        if evaluation is None:
            return Stage2AttachResult(
                REJECT_CONTACT_GEOMETRY, f"UID {target_uid} 无法计算接触几何")
        self.last_contact = evaluation
        if not evaluation.valid:
            if self.logger:
                self.logger.warning(
                    "[SUCTION] uid=%s 接触判据拒绝: %s",
                    target_uid, list(evaluation.reasons))
            return Stage2AttachResult(
                REJECT_CONTACT_GEOMETRY,
                f"外表面接触不合格: {list(evaluation.reasons)}",
                contact=evaluation)

        result = super().attach(target_uid, sim_time)
        if not result.ok:
            return Stage2AttachResult(result.code, result.message,
                                      contact=evaluation)
        rec = self.manager.by_uid(target_uid)
        transform = self._relative_transform(
            list(self.sim.getObjectPose(self.tcp_handle,
                                        self.sim.handle_world)),
            self.material_pose(rec.sim_handle))
        self.attach_transform = transform
        if self.logger:
            self.logger.info(
                "[SUCTION] uid=%s attach transform=%s gap=%.5f "
                "lateral=%.5f normal=%.5f",
                target_uid, [round(v, 5) for v in transform],
                evaluation.signed_distance, evaluation.lateral_offset,
                evaluation.normal_angle)
        return Stage2AttachResult(ATTACH_OK, result.message,
                                  contact=evaluation, transform=transform)

    # ── 释放 ────────────────────────────────────────────────

    def release(self, target_uid: int, sim_time: float,
                material_pose: list[float],
                support_z: float | None = None) -> ReleaseResult:
        rec = self.manager.by_uid(target_uid)
        if rec is None or rec.sim_handle is None:
            return Stage2ReleaseResult(False, f"UID {target_uid} 未登记")
        try:
            actual = self.material_pose(rec.sim_handle)
        except Exception as exc:
            return Stage2ReleaseResult(
                False, f"释放前无法读取真实物料位姿: {exc}")
        target = [float(v) for v in material_pose]
        correction_pos = math.dist(actual[:3], target[:3])
        correction_ang = quat_angle_between(actual[3:7], target[3:7])
        if correction_pos > cfg.RELEASE_POSE_CORRECTION_MAX or \
                correction_ang > cfg.RELEASE_ANGLE_CORRECTION_MAX:
            return Stage2ReleaseResult(
                False,
                f"RELEASE_CORRECTION_EXCEEDED: pos={correction_pos:.4f} m "
                f"ang={correction_ang:.4f} rad",
                correction_pos=correction_pos,
                correction_ang=correction_ang,
                release_pose=actual)

        # 保持世界位姿解绑: 传入真实位姿, 不写计划位姿
        result = super().release(target_uid, sim_time, actual,
                                 support_z=support_z)
        self.release_pose = actual
        self.release_target = target
        if self.logger:
            self.logger.info(
                "[SUCTION] uid=%s release correction pos=%.4f ang=%.4f "
                "(保持世界位姿解绑)", target_uid, correction_pos,
                correction_ang)
        return Stage2ReleaseResult(
            result.ok, result.message, stable=result.stable,
            penetration=result.penetration,
            correction_pos=correction_pos, correction_ang=correction_ang,
            release_pose=actual)

    # ── 工具变换 ────────────────────────────────────────────

    @staticmethod
    def _relative_transform(tcp_pose, material_pose) -> list[float]:
        """T_tcp^-1 * T_material, 供影子载荷复制使用."""
        p = [float(v) for v in tcp_pose]
        m = [float(v) for v in material_pose]
        rel = quat_rotate(quat_conjugate(p[3:7]),
                          [m[0] - p[0], m[1] - p[1], m[2] - p[2]])
        q = quat_multiply(quat_conjugate(p[3:7]), m[3:7])
        return [*rel, *q]
