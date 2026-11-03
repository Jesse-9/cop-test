"""unit_controller_dual.py — v1 双站层单工位控制器.

在运动层 `Stage2UnitController` 之上增加两个取料源:
  - `source="conveyor"`: 沿用基础/运动层传感器停带 → 实测位姿 → simIK 吸附;
  - `source="agv"`: 等待 AGV 在指定 dock 停稳, 校验同一 UID 的物料仍在
    载台有效区并稳定, 再按实测位姿吸附.

目的地仍由 `sink` 选择:
  - `sink="agv"`: 放到 AGV 载台 (A 工位装载);
  - `sink="station"`: 放到本工位置物台 (B 工位卸载/放置).

控制器**不驱动 AGV**: 双站层 AGV 由 `AGVRouteController` 单独执行路线;
控制器只在装载/卸载动作期间保持 AGV 停止, 避免基础层 `target=agv` 的
0.5 m 自走逻辑与两站转运冲突.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import config_base as base
from .material_manager import MaterialState
from .unit_controller_base import UnitTaskState, quat_tilt
from .unit_controller_motion import Stage2UnitController
from .motion.contact import contact_pose

from . import config_dual as cfg
from .agv_route import pose_yaw
from .layout_dual import UnitLayoutView


_AGV_HOLD_STATES = frozenset({
    UnitTaskState.APPROACHING,
    UnitTaskState.ATTACHING,
    UnitTaskState.LIFTING,
    UnitTaskState.TRANSFERRING,
    UnitTaskState.PLACING,
    UnitTaskState.RELEASING,
    UnitTaskState.SETTLING,
})


@dataclass
class TrayCheck:
    ok: bool
    detail: str = ""
    material_pose: Optional[list[float]] = None
    relative_xy: Optional[list[float]] = None
    z_error: Optional[float] = None


class Stage3UnitController(Stage2UnitController):
    """阶段三单工位控制器 (独立 IK/规划/碰撞/锁存由 runtime 注入)."""

    def __init__(self, *args, unit_id: str, source: str, sink: str,
                 dock_id: str, resources, task, **kwargs) -> None:
        if source not in ("conveyor", "agv"):
            raise ValueError(f"未知 source: {source!r}")
        if sink not in ("station", "agv"):
            raise ValueError(f"未知 sink: {sink!r}")
        kwargs["target"] = sink
        super().__init__(*args, **kwargs)
        self.unit_id = str(unit_id)
        self.source = str(source)
        self.sink = str(sink)
        self.dock_id = str(dock_id)
        self.resources = resources
        self.task = task

        self._tray_wait_started: Optional[float] = None
        self._tray_prev_pos: Optional[list[float]] = None
        self._tray_stable_count = 0
        self._tray_last_check: Optional[TrayCheck] = None
        self.release_attempted = False
        self._last_state = self.state

    # ── 生命周期 ────────────────────────────────────────────

    @property
    def finished(self) -> bool:
        if self.state is UnitTaskState.FAILED:
            return True
        return self.state is UnitTaskState.DONE

    @property
    def layout_view(self) -> UnitLayoutView:
        return self.layout  # type: ignore[return-value]

    # ── 取料源分流 ──────────────────────────────────────────

    def _update_conveyor(self) -> None:
        if self.source != "conveyor":
            return
        super()._update_conveyor()

    def _read_pick_sensor(self) -> None:
        if self.source != "conveyor":
            return
        super()._read_pick_sensor()

    def _advance_task(self) -> None:
        if (self.source == "agv"
                and self.state is UnitTaskState.WAITING_FOR_ITEM):
            self._advance_wait_agv_pick()
            return
        if self.state is UnitTaskState.RELEASING:
            self.release_attempted = True
        self._last_state = self.state
        super()._advance_task()

    # ── AGV 托盘取料 ────────────────────────────────────────

    def _advance_wait_agv_pick(self) -> None:
        if self._wait_started_at is None:
            self._wait_started_at = self.sim_time
        if self.sim_time - self._wait_started_at > self.pick_timeout:
            self._fail("AGV_PICK_TIMEOUT: AGV 未在限时内携带目标 UID 到站")
            return

        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if rec.state is MaterialState.READY_FOR_PICK:
            self._begin_motion(UnitTaskState.APPROACHING,
                               "tray material already measured")
            return
        if rec.state not in (MaterialState.ON_TRAY,
                             MaterialState.RELEASED):
            self._fail(
                f"MATERIAL_STATE_INVALID: uid={self.uid} 状态 "
                f"{rec.state.value} 不可从 AGV 取料")
            return

        docked = self.scene.agv_dock_hit(self.scene.agv_root)
        stopped = self._agv_stationary()
        upright = self._agv_upright()
        if not (docked and stopped and upright):
            self._tray_last_check = TrayCheck(
                False, f"dock_sensor={docked} stopped={stopped} "
                       f"upright={upright}")
            return

        check = self._check_material_on_tray(rec.sim_handle)
        self._tray_last_check = check
        if not check.ok:
            self._fail(f"LOAD_NOT_ON_TRAY: {check.detail}")
            return
        if rec.state is MaterialState.RELEASED:
            rec.transition_to(
                MaterialState.ON_TRAY, self.sim_time,
                f"stage3 confirmed on AGV tray at dock {self.dock_id}")

        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("LOAD_STATE_UNREADABLE: 无法读取载台物料位姿/速度")
            return
        step = (math.dist(position, self._tray_prev_pos)
                if self._tray_prev_pos is not None else 0.0)
        self._tray_prev_pos = list(position)
        if (speed <= base.MATERIAL_STABLE_SPEED_TOL
                and step <= base.MATERIAL_STABLE_POS_TOL):
            self._tray_stable_count += 1
        else:
            self._tray_stable_count = 0
        if self._tray_stable_count < base.MATERIAL_STABLE_STEPS:
            return

        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                rec.sim_handle, self.sim.handle_world)]
            box = self.suction.material_box(rec.sim_handle)
        except Exception as exc:
            self._fail(f"MATERIAL_GEOMETRY_UNREADABLE: {exc}")
            return
        tilt = quat_tilt(pose[3:7])
        if tilt > base.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 载台物料倾斜 {tilt:.3f} rad")
            return

        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                          "AGV tray material docked, stable and measured")
        self._event(
            f"AGV_TRAY_PICK: uid={self.uid} dock={self.dock_id} "
            f"pose=({pose[0]:.4f},{pose[1]:.4f},{pose[2]:.4f}) "
            f"tilt={tilt:.3f}")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose on AGV tray")

    def _check_material_on_tray(self, handle: int) -> TrayCheck:
        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                handle, self.sim.handle_world)]
            agv = [float(v) for v in self.agv.pose()]
        except Exception as exc:
            return TrayCheck(False, f"无法读取位姿: {exc}")

        yaw = pose_yaw(agv)
        dx = pose[0] - agv[0]
        dy = pose[1] - agv[1]
        c, s = math.cos(-yaw), math.sin(-yaw)
        local_x = c * dx - s * dy
        local_y = s * dx + c * dy
        z_error = pose[2] - (base.AGV_TRAY_TOP_Z + base.MATERIAL_HALF_HEIGHT)
        ok = (abs(local_x) <= cfg.AGV_TRAY_XY_TOL
              and abs(local_y) <= cfg.AGV_TRAY_XY_TOL
              and abs(z_error) <= cfg.AGV_TRAY_Z_TOL)
        return TrayCheck(
            ok, f"local=({local_x:.4f},{local_y:.4f}) "
                f"z_error={z_error:.4f}",
            material_pose=pose, relative_xy=[local_x, local_y],
            z_error=z_error)

    # ── AGV 保持静止, 不驱动 ────────────────────────────────

    def _update_agv(self) -> None:
        if self.agv is None:
            return
        if self.state in _AGV_HOLD_STATES:
            self.agv.stop()
        # 阶段三路线由 AGVRouteController 执行; 控制器绝不 set_cmd.

    # ── 摘要 ────────────────────────────────────────────────

    def summary(self) -> dict:
        data = super().summary()
        data.update({
            "unit_id": self.unit_id,
            "source": self.source,
            "sink": self.sink,
            "dock_id": self.dock_id,
            "task_id": getattr(self.task, "task_id", None),
            "release_attempted": bool(self.release_attempted),
            "tray_check": (None if self._tray_last_check is None else {
                "ok": self._tray_last_check.ok,
                "detail": self._tray_last_check.detail,
                "relative_xy": self._tray_last_check.relative_xy,
                "z_error": self._tray_last_check.z_error,
            }),
            "layout_view": self.layout.name,
        })
        return data
