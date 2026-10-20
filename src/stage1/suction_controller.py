"""suction_controller.py — 逻辑吸附与释放 (§2.9 / CLAUDE.md §6).

当前阶段不模拟真空压力。吸附条件是:
  UID 匹配 → 状态 READY_FOR_PICK → 吸盘传感器命中该 UID → TCP 与物料
  顶面中心距离 ≤ 容差 → 吸盘轴线倾角 ≤ 阈值 → 物料速度 ≤ 阈值。

吸附前保存父对象、位姿、static/respondable、质量、摩擦和碰撞掩码;
吸附期间只关闭物料动态响应 (static=1); 释放后完整恢复并 resetDynamicObject。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config as cfg
from .material_manager import InvalidTransition, MaterialState


# 结果码
ATTACH_OK = "ATTACH_OK"
REJECT_UID_UNKNOWN = "REJECT_UID_UNKNOWN"
REJECT_STATE = "REJECT_STATE"
REJECT_NO_SENSOR_HIT = "REJECT_NO_SENSOR_HIT"
REJECT_UID_MISMATCH = "REJECT_UID_MISMATCH"
REJECT_TCP_TOO_FAR = "REJECT_TCP_TOO_FAR"
REJECT_TOO_TILTED = "REJECT_TOO_TILTED"
REJECT_MOVING = "REJECT_MOVING"
REJECT_TOOL_MOVING = "REJECT_TOOL_MOVING"
REJECT_PHYSICS = "REJECT_PHYSICS"


@dataclass
class AttachResult:
    code: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code == ATTACH_OK


@dataclass
class ReleaseResult:
    ok: bool
    message: str = ""
    stable: bool = False
    penetration: bool = False


class SuctionController:
    def __init__(self, sim, manager, tcp_handle: int, sensor_handle: int,
                 link_handle: int = -1, release_parent: int = -1,
                 logger=None) -> None:
        self.sim = sim
        self.manager = manager
        self.tcp_handle = int(tcp_handle)
        self.sensor_handle = int(sensor_handle)
        self.link_handle = int(link_handle)
        self.release_parent = int(release_parent)
        self.logger = logger
        self._snapshots: dict[int, dict] = {}

    # ── 吸附 ────────────────────────────────────────────────

    def attach(self, target_uid: int, sim_time: float) -> AttachResult:
        rec = self.manager.by_uid(target_uid)
        if rec is None:
            return AttachResult(REJECT_UID_UNKNOWN, f"UID {target_uid} 未登记")
        if rec.state is not MaterialState.READY_FOR_PICK:
            return AttachResult(
                REJECT_STATE,
                f"物料 {target_uid} 状态 {rec.state.value} 不可吸附")
        handle = rec.sim_handle
        if handle is None or handle < 0:
            return AttachResult(REJECT_UID_UNKNOWN,
                                f"物料 {target_uid} 没有场景句柄")

        hit = self._sensor_hit(handle)
        if hit is None:
            return AttachResult(REJECT_NO_SENSOR_HIT, "吸盘传感器无读数")
        if int(hit) != int(handle):
            return AttachResult(
                REJECT_UID_MISMATCH,
                f"传感器命中 {hit}, 当前目标 uid={target_uid} handle={handle}")

        gap = self._contact_gap(handle)
        if gap > cfg.SUCTION_CONTACT_TOL:
            return AttachResult(
                REJECT_TCP_TOO_FAR,
                f"TCP 距物料顶面 {gap:.4f} m > {cfg.SUCTION_CONTACT_TOL:.4f} m")

        tilt = self._tool_tilt()
        if tilt > cfg.SUCTION_TILT_TOL:
            return AttachResult(
                REJECT_TOO_TILTED,
                f"吸盘倾角 {tilt:.3f} rad > {cfg.SUCTION_TILT_TOL:.3f} rad")

        speed = self._linear_speed(handle)
        if speed > cfg.SUCTION_MAX_ATTACH_SPEED:
            return AttachResult(
                REJECT_MOVING,
                f"物料移动中 ({speed:.3f} m/s > "
                f"{cfg.SUCTION_MAX_ATTACH_SPEED:.3f} m/s)")

        tool_speed = self._linear_speed(self.tcp_handle)
        if tool_speed > cfg.SUCTION_MAX_TOOL_SPEED:
            return AttachResult(
                REJECT_TOOL_MOVING,
                f"工具仍在移动 ({tool_speed:.3f} m/s > "
                f"{cfg.SUCTION_MAX_TOOL_SPEED:.3f} m/s)")

        snap = self._save_snapshot(handle)
        try:
            self.sim.setObjectParent(handle, self.tcp_handle, True)
            # setObjectParent 可能重算 static, 挂父后显式关闭动态响应
            self.sim.setObjectInt32Param(handle, self.sim.shapeintparam_static, 1)
        except Exception as e:
            return AttachResult(REJECT_PHYSICS, f"绑定/关闭动态失败: {e}")

        self.manager.set_parent(target_uid, self.tcp_handle)
        rec.transition_to(MaterialState.HELD_BY_SUCTION, sim_time,
                          "suction attach (TCP + sensor UID confirmed)")
        self._snapshots[target_uid] = snap
        if self.logger:
            self.logger.info(
                "[SUCTION] uid=%s 已吸附 handle=%s parent=%s static=%s "
                "respondable=%s mass=%s friction=%s mask=%s",
                target_uid, handle, snap["parent"], snap["static"],
                snap["respondable"], snap["mass"], snap["friction"],
                snap["mask"])
        return AttachResult(ATTACH_OK, "吸附成功")

    # ── 释放 ────────────────────────────────────────────────

    def release(self, target_uid: int, sim_time: float,
                material_pose: list[float],
                support_z: float | None = None) -> ReleaseResult:
        rec = self.manager.by_uid(target_uid)
        if rec is None:
            return ReleaseResult(False, f"UID {target_uid} 未登记")
        if rec.state is not MaterialState.HELD_BY_SUCTION:
            raise InvalidTransition(
                f"物料 {target_uid} 未处于 HELD_BY_SUCTION "
                f"(当前 {rec.state.value}), 不能释放")
        handle = rec.sim_handle
        snap = self._snapshots.get(target_uid, {})

        # 1) 先设置目标位姿, 再恢复物理属性 (§2.9 步骤8)
        self.sim.setObjectPose(handle, list(material_pose),
                               self.sim.handle_world)
        rec.transition_to(MaterialState.ON_TRAY, sim_time,
                          "placed at target pose")

        # 2) 恢复到原始父对象 (失败必须显式返回, 不能假装完成)
        parent = int(snap.get("parent", self.release_parent))
        try:
            self.sim.setObjectParent(handle, parent, True)
        except Exception as exc:
            return ReleaseResult(False, f"恢复父对象失败: {exc}")
        self.manager.set_parent(target_uid, parent)

        # 3) 恢复 dynamic / respondable / mass / friction / mask
        if not self._restore(snap):
            return ReleaseResult(False, "恢复物料物理属性失败")

        # 4) 重置动力学对象, 让物料重新参与重力/接触/摩擦
        try:
            self.sim.resetDynamicObject(handle)
        except Exception:
            pass

        speed = self._linear_speed(handle)
        stable = speed <= cfg.SUCTION_RELEASE_MAX_SPEED

        support = (min(cfg.STATION_TOP_Z, cfg.AGV_TRAY_TOP_Z)
                   if support_z is None else float(support_z))
        penetration = (float(material_pose[2]) - cfg.MATERIAL_HALF_HEIGHT
                       < support - cfg.SUCTION_SETTLE_Z_TOL)

        rec.transition_to(MaterialState.RELEASED, sim_time,
                          f"released speed={speed:.3f} stable={stable} "
                          f"penetration={penetration}")
        if self.logger:
            self.logger.info(
                "[SUCTION] uid=%s 已释放 speed=%.3f stable=%s penetration=%s",
                target_uid, speed, stable, penetration)
        return ReleaseResult(True, "释放完成", stable=stable,
                             penetration=penetration)

    # ── 快照 / 恢复 ─────────────────────────────────────────

    def snapshot(self, uid: int):
        return self._snapshots.get(uid)

    def forget(self, uid: int) -> None:
        """试验清理: 物料对象移除后丢弃其属性快照。"""
        self._snapshots.pop(int(uid), None)

    def _save_snapshot(self, handle: int) -> dict:
        sim = self.sim
        snap = {
            "handle": int(handle),
            "parent": -1,
            "pose": [0.0] * 7,
            "static": 0,
            "respondable": 1,
            "mass": None,
            "friction": None,
            "mask": None,
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
            snap["static"] = int(sim.getObjectInt32Param(
                handle, sim.shapeintparam_static))
        except Exception:
            pass
        try:
            snap["respondable"] = int(sim.getObjectInt32Param(
                handle, sim.shapeintparam_respondable))
        except Exception:
            pass
        try:
            snap["mask"] = int(sim.getObjectInt32Param(
                handle, sim.shapeintparam_respondable_mask))
        except Exception:
            pass
        try:
            snap["mass"] = float(sim.getObjectFloatParam(
                handle, sim.shapefloatparam_mass))
        except Exception:
            pass
        for prop in ("bullet.friction", "ode.friction"):
            try:
                snap["friction"] = float(sim.getFloatProperty(handle, prop))
                break
            except Exception:
                continue
        return snap

    def _restore(self, snap: dict) -> bool:
        sim = self.sim
        handle = snap.get("handle")
        if handle is None:
            return False
        ok = True
        try:
            sim.setObjectInt32Param(handle, sim.shapeintparam_static,
                                    int(snap.get("static", 0)))
        except Exception:
            ok = False
        try:
            sim.setObjectInt32Param(handle, sim.shapeintparam_respondable,
                                    int(snap.get("respondable", 1)))
        except Exception:
            ok = False
        if snap.get("mass") is not None:
            try:
                sim.setObjectFloatParam(handle, sim.shapefloatparam_mass,
                                        float(snap["mass"]))
            except Exception:
                pass
        if snap.get("mask") is not None:
            try:
                sim.setObjectInt32Param(
                    handle, sim.shapeintparam_respondable_mask,
                    int(snap["mask"]))
            except Exception:
                pass
        if snap.get("friction") is not None:
            for prop in ("bullet.friction", "ode.friction"):
                try:
                    sim.setFloatProperty(handle, prop, float(snap["friction"]))
                    break
                except Exception:
                    continue
        return ok

    # ── 读数 ────────────────────────────────────────────────

    def _sensor_hit(self, entity: int) -> int | None:
        """只检测当前任务物料句柄 — 避免命中机械臂/吸盘自身几何。"""
        try:
            res = self.sim.checkProximitySensor(self.sensor_handle, int(entity))
        except Exception:
            return None
        if isinstance(res, (tuple, list)) and len(res) >= 4:
            return _parse_hit(res)
        return int(res) if res not in (None, 0, -1) else None

    def _tool_tilt(self) -> float:
        q = self.sim.getObjectQuaternion(self.tcp_handle, self.sim.handle_world)
        x, y, z, w = [float(v) for v in q[:4]]
        zz = 1.0 - 2.0 * (x * x + y * y)
        return math.acos(max(-1.0, min(1.0, zz)))

    def _contact_gap(self, material_handle: int) -> float:
        p_tcp = self.sim.getObjectPosition(self.tcp_handle, self.sim.handle_world)
        p_mat = self.sim.getObjectPosition(material_handle, self.sim.handle_world)
        top = [p_mat[0], p_mat[1], p_mat[2] + cfg.MATERIAL_HALF_HEIGHT]
        return math.dist(list(p_tcp), top)

    def _linear_speed(self, handle: int) -> float:
        v = self.sim.getObjectVelocity(handle)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            lin = v[0]
        else:
            lin = list(v)[:3]
        return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))


def _parse_hit(res) -> int | None:
    # 4.10 checkProximitySensor: (result, distance, point, objectHandle, normal)
    if len(res) >= 4:
        hit = res[3]
        if res[0] and hit is not None and int(hit) >= 0:
            return int(hit)
    return None
