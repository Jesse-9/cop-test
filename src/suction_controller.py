"""suction_controller.py — 逻辑吸附与释放 (阶段一 §2.9 / CLAUDE §6)

当前阶段不模拟真实真空压力。吸附 = "TCP 到位 + suction sensor 命中指定
UID + 姿态/速度/碰撞检查" 的逻辑流程 (§6)。

关键约束:
  - 吸附前保存物料父对象、位姿、dynamic/respondable、质量、摩擦、掩码 (§2.9 步骤2)
  - 吸附期间仅关闭物料动态响应; 释放后完整恢复并 resetDynamicObject (§2.9 步骤9)
  - 传感器命中 ≠ 自动吸附: 必须校验当前任务 UID (§2.8 步骤4-5)
  - 吸附/释放与状态转换写入同一 UID 的生命周期日志 (§6)

方案依据:
  docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.9
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import factory_config as cfg
from material_manager import MaterialState, InvalidTransition


# ═══════════════════════════════════════════════════════════════
# 结果码
# ═══════════════════════════════════════════════════════════════

ATTACH_OK              = "ATTACH_OK"
REJECT_UID_UNKNOWN     = "REJECT_UID_UNKNOWN"
REJECT_STATE           = "REJECT_STATE"
REJECT_NO_SENSOR_HIT   = "REJECT_NO_SENSOR_HIT"
REJECT_UID_MISMATCH    = "REJECT_UID_MISMATCH"   # §2.8 步骤5 PICK_MISMATCH
REJECT_TCP_TOO_FAR     = "REJECT_TCP_TOO_FAR"
REJECT_TOO_TILTED      = "REJECT_TOO_TILTED"
REJECT_MOVING          = "REJECT_MOVING"
REJECT_NO_PHYSICS      = "REJECT_NO_PHYSICS"


class AttachRejected(Exception):
    """吸附前置检查未通过 (附带 result code)"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class AttachResult:
    code: str
    ok: bool = field(default=False)
    message: str = ""

    def __post_init__(self):
        self.ok = (self.code == ATTACH_OK)


@dataclass
class ReleaseResult:
    ok: bool
    message: str = ""
    stable: bool = False     # §2.9 步骤10 释放后稳定性检查
    penetration: bool = False


# ═══════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════

class SuctionController:
    """逻辑吸附/释放控制器 (单吸盘)。

    Args:
        sim:            Remote API sim 模块。
        manager:        MaterialManager (UID 唯一入口)。
        link_handle:    吸盘 Link (力传感器, 吸附参考点) 句柄。
        tcp_handle:     SuctionPad_*_TCP dummy 句柄。
        sensor_handle:  吸盘接近传感器句柄 (只读确认, 不触发吸附)。
        logger:         可选日志。
    """

    def __init__(self, sim, manager, link_handle: int, tcp_handle: int,
                 sensor_handle: int, logger=None):
        self.sim = sim
        self.manager = manager
        self.link_handle = link_handle
        self.tcp_handle = tcp_handle
        self.sensor_handle = sensor_handle
        self.logger = logger
        self._snapshots: dict[int, dict] = {}
        self._sensor_hit = self._read_sensor_hit
        self._tcp_tilt = self._read_tcp_tilt

    # ── 吸附 ────────────────────────────────────────────────────

    def attach(self, target_uid: int, sim_time: float) -> AttachResult:
        """执行吸附前置检查并绑定物料到吸盘 (§2.9 步骤1-5)。

        检查顺序: UID 存在 → 状态可吸附 → sensor 命中 → UID 匹配 → TCP 距离
        → 倾角 → 速度 → 物理属性快照。
        """
        rec = self.manager.by_uid(target_uid)
        if rec is None:
            return AttachResult(REJECT_UID_UNKNOWN, message=f"UID {target_uid} 未登记")

        if rec.state is not MaterialState.READY_FOR_PICK:
            return AttachResult(
                REJECT_STATE,
                message=f"物料 {target_uid} 状态 {rec.state.value} 不可吸附")

        mat_handle = rec.sim_handle
        if mat_handle is None or mat_handle < 0:
            return AttachResult(REJECT_UID_UNKNOWN,
                                message=f"物料 {target_uid} 无场景句柄")

        # 传感器读数与任务判断分开记录 (§2.8 步骤8)
        hit = self._sensor_hit()
        if hit is None or hit < 0:
            return AttachResult(REJECT_NO_SENSOR_HIT,
                                message="吸盘传感器无读数")
        if int(hit) != int(mat_handle):
            return AttachResult(
                REJECT_UID_MISMATCH,
                message=f"传感器命中 {hit}, 当前任务目标 {mat_handle} "
                        f"(uid={target_uid})")

        # TCP 与物料距离
        gap = self._tcp_distance(mat_handle)
        if gap > cfg.SUCTION_HOLD_TOLERANCE:
            return AttachResult(
                REJECT_TCP_TOO_FAR,
                message=f"TCP 距物料 {gap:.3f} m > {cfg.SUCTION_HOLD_TOLERANCE} m")

        # 倾角 (§5: 吸盘翻转则拒绝)
        tilt = self._tcp_tilt()
        if tilt > cfg.ARM_TOOL_MAX_TILT:
            return AttachResult(
                REJECT_TOO_TILTED,
                message=f"吸盘倾角 {tilt:.3f} rad > {cfg.ARM_TOOL_MAX_TILT} rad")

        # 物料速度 (运动中的物料不允许吸附)
        try:
            v = self.sim.getObjectVelocity(mat_handle)
            speed = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        except Exception:
            speed = 0.0
        if speed > cfg.SUCTION_MAX_ATTACH_SPEED:
            return AttachResult(
                REJECT_MOVING,
                message=f"物料移动中 ({speed:.3f} m/s)")

        # ── 快照 (§2.9 步骤2) ──
        snap = self._save_snapshot(mat_handle, rec)

        # ── 绑定到吸盘 Link (§2.9 步骤3) ──
        try:
            self.sim.setObjectParent(mat_handle, self.link_handle, True)
        except Exception as e:
            return AttachResult(REJECT_NO_PHYSICS, message=f"绑定失败: {e}")

        # ── 关闭动态响应 (§2.9 步骤4) ──
        self._set_dynamic(mat_handle, False)

        # ── 状态转换 + 生命周期日志 (§2.9 步骤5) ──
        rec.transition_to(MaterialState.HELD_BY_SUCTION, sim_time,
                          reason=f"attached to suction TCP (snapshot saved)")
        self.manager.set_parent(target_uid, self.link_handle)

        # 记录物料的原始属性快照, 供 release 恢复
        self._snapshots[target_uid] = snap
        if self.logger:
            self.logger.info(
                f"[SUCTION] uid={target_uid} 已吸附 (parent={snap['parent']}, "
                f"static={snap['static']}, respondable={snap['respondable']}, "
                f"mass={snap['mass']})")
        return AttachResult(ATTACH_OK, message="吸附成功")

    # ── 释放 ────────────────────────────────────────────────────

    def release(self, target_uid: int, sim_time: float,
                place_pose: list[float]) -> ReleaseResult:
        """放置并释放 (§2.9 步骤7-11)。

        顺序: 设置目标位姿 → 恢复 dynamic/respondable/父对象 → resetDynamicObject
        → 状态 ON_TRAY → 稳定性检查 → RELEASED。
        """
        rec = self.manager.by_uid(target_uid)
        if rec is None:
            return ReleaseResult(False, f"UID {target_uid} 未登记")
        if rec.state is not MaterialState.HELD_BY_SUCTION:
            raise InvalidTransition(
                f"物料 {target_uid} 未处于 HELD_BY_SUCTION (当前 {rec.state.value}), "
                f"不能释放")

        mat_handle = rec.sim_handle
        snap = self._snapshots.get(target_uid, {})

        # §2.9 步骤8: 先设置目标位姿, 再恢复物理属性
        try:
            self.sim.setObjectPose(mat_handle, place_pose, self.sim.handle_world)
        except Exception:
            pass

        # 状态: HELD_BY_SUCTION → ON_TRAY (放置到工位/载台)
        rec.transition_to(MaterialState.ON_TRAY, sim_time,
                          reason=f"placed at tray, pose set")

        # 恢复父对象 (回到原父, 通常是场景根)
        parent = snap.get("parent", -1)
        try:
            self.sim.setObjectParent(mat_handle, parent, True)
        except Exception:
            pass

        # 恢复 dynamic/respondable (§2.9 步骤8)
        self._restore_physics(mat_handle, snap)

        # §2.9 步骤9: resetDynamicObject
        try:
            self.sim.resetDynamicObject(mat_handle)
        except Exception:
            pass

        # §2.9 步骤10: 稳定性检查 (速度阈值)
        try:
            v = self.sim.getObjectVelocity(mat_handle)
            speed = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
        except Exception:
            speed = 0.0
        stable = speed <= cfg.SUCTION_RELEASE_MAX_SPEED

        # 状态: ON_TRAY → RELEASED
        rec.transition_to(MaterialState.RELEASED, sim_time,
                          reason=f"released (speed={speed:.3f} m/s, stable={stable})")
        self.manager.detach_sim(target_uid) if speed > cfg.SUCTION_RELEASE_MAX_SPEED else None
        if self.logger:
            self.logger.info(
                f"[SUCTION] uid={target_uid} 已释放 (speed={speed:.3f}, stable={stable})")
        return ReleaseResult(True, "释放完成", stable=stable,
                             penetration=False)

    # ── 快照 ────────────────────────────────────────────────────

    def snapshot(self, uid: int) -> Optional[dict]:
        return self._snapshots.get(uid)

    def _save_snapshot(self, handle: int, rec) -> dict:
        """保存原始物理属性快照 (§2.9 步骤2 / §6)"""
        sim = self.sim
        snap = {
            "handle": handle,
            "parent": -1,
            "pose": [0.0] * 7,
            "static": 0,
            "respondable": 1,
            "mass": None,
        }
        try:
            snap["parent"] = int(sim.getObjectParent(handle))
        except Exception:
            pass
        try:
            snap["pose"] = list(sim.getObjectPose(handle, sim.handle_world))
        except Exception:
            pass
        try:
            snap["static"] = int(sim.getObjectInt32Parameter(
                handle, sim.shapeintparam_static))
        except Exception:
            pass
        try:
            snap["respondable"] = int(sim.getObjectInt32Parameter(
                handle, sim.shapeintparam_respondable))
        except Exception:
            pass
        try:
            v = sim.getObjectFloatParameter(handle, sim.shapefloatparam_mass)
            snap["mass"] = v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else v
        except Exception:
            pass
        return snap

    # ── 物理属性控制 ─────────────────────────────────────────────

    def _set_dynamic(self, handle: int, dynamic: bool) -> None:
        """启/停物料的动态响应。4.10 实测 static property 不可用, 走 int-param。"""
        try:
            self.sim.setObjectInt32Param(handle, self.sim.shapeintparam_static,
                                         0 if dynamic else 1)
        except Exception:
            pass

    def _restore_physics(self, handle: int, snap: dict) -> None:
        """恢复快照中的 dynamic/respondable/mass (§2.9 步骤8)。"""
        sim = self.sim
        try:
            sim.setObjectInt32Param(handle, sim.shapeintparam_static,
                                    int(snap.get("static", 0)))
        except Exception:
            pass
        try:
            sim.setObjectInt32Param(handle, sim.shapeintparam_respondable,
                                    int(snap.get("respondable", 1)))
        except Exception:
            pass
        mass = snap.get("mass")
        if mass is not None:
            try:
                sim.setObjectFloatParam(handle, sim.shapefloatparam_mass, float(mass))
            except Exception:
                pass

    # ── sensor / TCP 读数 (可注入, 便于离线测试) ────────────────

    def _read_sensor_hit(self) -> int:
        """读取吸盘接近传感器的命中句柄; 无命中返回 -1。

        §2.8 步骤6: Sensor_SuctionPad 只用于确认距离和接触方向,
        不做任何自动吸附动作。
        """
        sim = self.sim
        try:
            res = sim.checkProximitySensor(self.sensor_handle, sim.handle_all)
            # 4.10 返回 (count, handle, distance, point, normal) 变体, 统一解析
            if isinstance(res, (tuple, list)) and len(res) >= 2:
                count = res[0]
                if count and res[1] is not None:
                    return int(res[1])
            return -1
        except Exception:
            return -1

    def _read_tcp_tilt(self) -> float:
        """吸盘轴线偏离竖直 (世界 -Z) 的角度 (rad)。

        用 TCP 与 Link 的相对 X 轴/朝向计算; 简化实现读取 TCP 世界四元数
        并求其 Z 轴与竖直方向的夹角。
        """
        sim = self.sim
        try:
            q = sim.getObjectQuaternion(self.tcp_handle, sim.handle_world)
        except Exception:
            return 0.0
        # 四元数转 Z 轴向量
        x, y, z, w = q[0], q[1], q[2], q[3]
        zx = 2 * (x * z + w * y)
        zy = 2 * (y * z - w * x)
        zz = 1 - 2 * (x * x + y * y)
        # 与 -Z 的夹角
        cos_t = max(-1.0, min(1.0, -zz))
        return math.acos(cos_t)

    def _tcp_distance(self, mat_handle: int) -> float:
        """TCP 与物料中心的距离 (m)"""
        sim = self.sim
        try:
            p_tcp = sim.getObjectPosition(self.tcp_handle, sim.handle_world)
            p_mat = sim.getObjectPosition(mat_handle, sim.handle_world)
            return math.sqrt(sum((p_tcp[k] - p_mat[k]) ** 2 for k in range(3)))
        except Exception:
            return 0.0