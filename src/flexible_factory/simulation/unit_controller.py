"""unit_controller.py — v1 UFLOW 单岛控制器.

在双站层 :class:`flexible_factory.simulation.unit_controller_dual.Stage3UnitController`
之上扩展三种取料源与三种放置汇:

取料源:
  - ``conveyor``: 本岛入口传送带 (A/B/D/E 各一条线), 传感器命中目标 UID
    后主动停带 → 稳定 → 实测位姿 → simIK 吸附;
  - ``agv``: AGV 在指定 dock 停稳, 校验同一 UID 仍在载台后吸附
    (A 卸载 SEMI_AB, E 卸载 PART_DE);
  - ``station``: A→C 固定交接台, 交接传感器命中且 UID/类型/稳定性一致后
    按实测位姿吸附 (C)。

放置汇:
  - ``station``: 本岛台面/交接台/E 缓冲 (物料释放到静态承载面);
  - ``agv``: AGV 载台 (B 装载 SEMI_AB, D 装载 PART_DE);
  - ``conveyor``: 传送带入口 (C 放到 C→E 线; 放料前必须停带并确认)。

控制器**不驱动 AGV**; UFLOW AGV 由协调器航线控制器独占. 所有线与臂的
启停都由协调器按资源表调度, 控制器只执行本任务.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import config_base as base
from .material_manager import MaterialState
from .unit_controller_base import UnitTaskState, quat_tilt
from . import config_motion as s2cfg
from .motion.contact import contact_pose
from .motion.conveyor_safety import read_conveyor_speed
from .motion.path_planning import Goal, MotionContext, PlanStatus
from .motion.trajectory import joint_distance
from .unit_controller_motion import (ConveyorPickContext, Stage2TaskState,
                                    _motion_params,
                                    horizontal_transfer_check, lift_pose,
                                    pre_place_pose)
from .unit_controller_dual import Stage3UnitController

from . import config as cfg
from .layout import LineSpec, UnitView4
from ..domain.materials import ItemType, MaterialRegistry

# A 臂 AGV 取料的 pre_pick 分支完整链复核上限 (见 _select_pre_pick_branch)
_APPROACH_BRANCH_MAX = 4


def agv_material_position(tray_pose):
    """托盘实测位姿 → 物料中心 (托盘顶面 + 物料半高)."""
    return [float(tray_pose[0]), float(tray_pose[1]),
            float(tray_pose[2]) + base.AGV_TRAY_THICKNESS / 2.0
            + base.MATERIAL_HALF_HEIGHT]


def agv_place_pose(nominal_pose, tray_pose):
    """AGV 载台放置点: 跟随实测托盘中心, 保持标称竖向偏移与工具姿态.

    AGV 停靠误差可达数厘米; 固定标称放置点会让载荷降入托盘时蹭到
    托盘壁 (实测 `AGV_TrayWall_Front`). 托盘是 AGV 的子对象, 其世界
    位姿就是当前载台中心; 竖向偏移按托盘厚度与物料半高计算.
    """
    position = agv_material_position(tray_pose)
    return [position[0], position[1],
            position[2] + base.MATERIAL_HALF_HEIGHT,
            *[float(v) for v in nominal_pose[3:7]]]


def agv_material_pose(nominal_pose, tray_pose):
    """与 :func:`agv_place_pose` 同源的释放目标物料位姿.

    释放校验比较实测物料位姿与目标; 放置点跟随托盘后, 目标也必须
    用同一托盘中心, 否则会被判为修正超限.
    """
    position = agv_material_position(tray_pose)
    return [*position, *[float(v) for v in nominal_pose[3:7]]]


class Stage4UnitControllerError(RuntimeError):
    code = "STAGE4_UNIT"


@dataclass(frozen=True)
class SinkSpec:
    """本任务的放置目标 (台面/AGV/传送带) 与承载面高度."""

    kind: str
    tcp_pose: tuple[float, ...]
    material_pose: tuple[float, ...]
    support_z: float
    zone: str
    line: Optional[LineSpec] = None


class TaskLayout:
    """控制器期望的最小 layout 接口 (sink 专属位姿 + 承载面高度)."""

    def __init__(self, name: str, sink: SinkSpec, unit_view: UnitView4,
                 target: str) -> None:
        self.name = name
        self.sink = sink
        self.unit_view = unit_view
        self._label = target

    @property
    def station_top_z(self) -> float:
        return float(self.sink.support_z)

    @property
    def arm_base(self):
        return self.unit_view.arm_base

    def tcp_pose_for_target(self, target: str) -> list[float]:
        return [float(v) for v in self.sink.tcp_pose]

    def material_pose_for_target(self, target: str) -> list[float]:
        values = [float(v) for v in self.sink.material_pose]
        if len(values) == 3:
            values.extend(base.TOOL_TARGET_QUATERNION)
        return values

    def targets(self) -> dict[str, list[float]]:
        return {self._label: list(self.sink.tcp_pose)}


class Stage4UnitController(Stage3UnitController):
    """阶段四单岛控制器 (独立 IK/规划/碰撞/锁存由 runtime 注入)."""

    def __init__(self, *args, unit_id: str, source: str, sink: str,
                 dock_id: str, resources, task, line: LineSpec,
                 sink_spec: SinkSpec, flow: MaterialRegistry,
                 expected_types, park_tcp_pose=None, **kwargs) -> None:
        if source not in ("conveyor", "agv", "station"):
            raise ValueError(f"未知 source: {source!r}")
        if sink not in ("station", "agv", "conveyor"):
            raise ValueError(f"未知 sink: {sink!r}")
        target_label = "agv" if sink == "agv" else "station"
        kwargs["target"] = target_label
        # 阶段三构造器只接受 conveyor/agv 源与 station/agv 汇; station 源和
        # conveyor 汇在构造后再启用, 仍由本类的分流/放置覆盖实现.
        passthrough_source = "conveyor" if source == "station" else source
        passthrough_sink = "agv" if sink == "agv" else "station"
        super().__init__(*args, unit_id=unit_id,
                         source=passthrough_source, sink=passthrough_sink,
                         dock_id=dock_id, resources=resources, task=task,
                         **kwargs)
        self.source = source
        self.sink = sink
        self.target = target_label
        self.line = line
        self.sink_spec = sink_spec
        self.flow = flow
        self.expected_types = tuple(ItemType(item) for item in expected_types)
        self.zone = str(sink_spec.zone)
        self.park_tcp_pose = (None if park_tcp_pose is None
                              else [float(v) for v in park_tcp_pose])
        self.line_fault = False
        self._line_stop_count = 0
        self._line_stop_since: Optional[float] = None
        self._type_checked = False

    # ═══════════════════════════════════════════════════════
    # 取料源分流
    # ═══════════════════════════════════════════════════════

    def _advance_task(self) -> None:
        if (self.source == "station"
                and self.state is UnitTaskState.WAITING_FOR_ITEM):
            self._advance_wait_station_pick()
            return
        if (self.source == "agv"
                and self.state is UnitTaskState.WAITING_FOR_ITEM):
            self._advance_wait_agv_pick()
            return
        if self.state is UnitTaskState.RELEASING:
            self.release_attempted = True
        super()._advance_task()

    # ── 传送带取料: 本岛线别名 ──────────────────────────────

    def _update_conveyor(self) -> None:
        if self.line_fault:
            return
        super()._update_conveyor()

    def _conveyor_pick_alias(self) -> str:
        return self.line.conveyor

    def _pick_context(self) -> ConveyorPickContext:
        return ConveyorPickContext(
            conveyor_alias=self._conveyor_pick_alias(),
            belt_geometry=self.line.geometry,
            belt_speed=cfg.LINE_SPEED,
            pick_region=self.line.pick_region,
            sensor_alias=self.line.sensor_alias,
            label=f"{self.line.line_id} ")

    def _check_pick_candidate(self, rec, pose) -> bool:
        return self._assert_expected_type()

    # ── 固定交接台取料 ──────────────────────────────────────

    def _advance_wait_station_pick(self) -> None:
        if self._wait_started_at is None:
            self._wait_started_at = self.sim_time
        if self.sim_time - self._wait_started_at > self.pick_timeout:
            self._fail("STATION_PICK_TIMEOUT: 交接台未在限时内提供目标 UID")
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if rec.state is MaterialState.READY_FOR_PICK:
            self._begin_motion(UnitTaskState.APPROACHING,
                               "handoff material already measured")
            return
        if rec.state not in (MaterialState.RELEASED,):
            self._fail(
                f"MATERIAL_STATE_INVALID: uid={self.uid} 状态 "
                f"{rec.state.value} 不可从固定交接台取料")
            return
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return
        if flow.zone != cfg.HANDOFF_AC["id"]:
            self._fail(f"ZONE_MISMATCH: uid={self.uid} 不在 A→C 交接台 "
                       f"(zone={flow.zone})")
            return
        if self._assert_expected_type():
            return
        hit = self.scene.check_sensor(cfg.HANDOFF_AC["sensor"],
                                      rec.sim_handle)
        if hit is None:
            return
        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("STATION_MATERIAL_UNREADABLE")
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
            self._fail(f"MATERIAL_TILT: 交接物料倾斜 {tilt:.3f} rad")
            return
        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                          "handoff material present, stable and measured")
        self._event(
            f"STATION_PICK: uid={self.uid} zone={flow.zone} "
            f"pose=({pose[0]:.4f},{pose[1]:.4f},{pose[2]:.4f})")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured handoff pose")

    # ── AGV 载台取料 (按本岛 dock) ──────────────────────────

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
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return
        if flow.zone != "agv":
            self._fail(f"ZONE_MISMATCH: uid={self.uid} 不在 AGV 载台 "
                       f"(zone={flow.zone})")
            return
        if self._assert_expected_type():
            return
        docked = self.scene.agv_dock_hit(self.scene.agv_root)
        stopped = self._agv_stationary()
        upright = self._agv_upright()
        if not (docked and stopped and upright):
            return
        check = self._check_material_on_tray(rec.sim_handle)
        self._tray_last_check = check
        if not check.ok:
            self._fail(f"LOAD_NOT_ON_TRAY: {check.detail}")
            return
        if rec.state is MaterialState.RELEASED:
            rec.transition_to(MaterialState.ON_TRAY, self.sim_time,
                              f"stage4 confirmed on AGV tray at dock "
                              f"{self.dock_id}")
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
            f"pose=({pose[0]:.4f},{pose[1]:.4f},{pose[2]:.4f})")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose on AGV tray")

    # ── 类型校验 ────────────────────────────────────────────

    def _assert_expected_type(self) -> bool:
        """返回 True 表示失败 (已记录), 便于调用方提前返回."""
        flow = self.flow.by_uid(self.uid)
        if flow is None:
            self._fail("FLOW_RECORD_MISSING")
            return True
        if flow.item_type not in self.expected_types:
            self._fail(
                f"ITEM_TYPE_MISMATCH: uid={self.uid} 类型 "
                f"{flow.item_type.value} 不在本工位允许 "
                f"{[item.value for item in self.expected_types]}")
            return True
        return False

    # ── 放置 ────────────────────────────────────────────────

    def _place_pose(self) -> list[float]:
        """AGV 放置点跟随实测托盘, 其余汇沿用布局标称位姿."""
        pose = super()._place_pose()
        if self.sink != "agv":
            return pose
        try:
            tray = self.scene.handle(base.ALIASES["agv_tray"])
            if int(tray) < 0:
                return pose
            tray_pose = self.sim.getObjectPose(tray, self.sim.handle_world)
        except Exception:
            return pose
        return agv_place_pose(pose, tray_pose)

    def _material_pose_for_target(self) -> list[float]:
        """释放目标物料位姿必须与 AGV 放置点同源 (实测托盘)."""
        pose = super()._material_pose_for_target()
        if self.sink != "agv":
            return pose
        try:
            tray = self.scene.handle(base.ALIASES["agv_tray"])
            if int(tray) < 0:
                return pose
            tray_pose = self.sim.getObjectPose(tray, self.sim.handle_world)
        except Exception:
            return pose
        return agv_material_pose(pose, tray_pose)

    def _transfer_goal_variants(self, place, start_pose):
        """A 臂从 AGV 卸货到交接台: 增加同高度 via-park 候选.

        06-15 的直线/±0.30 m 绕行在 A 臂由不同前置任务进入 AGV 取料时,
        可能落在不同的腕部分支: 实测 staggered_batch 的 a_agv 水平直连
        横向偏差 8.1 mm (>5 mm), +0.30 绕行 IK 无解, -0.30 绕行偏差
        5.0 mm 仍被拒绝。这里追加一个同高度、同工具姿态的 via-park
        候选 (交接台西南侧 park 上方), 仍由 ``direct_only=True`` 的
        两段笛卡尔水平线规划, 使用同一 IK/碰撞/分支/水平姿态复核;
        仅对 A 臂 AGV→交接台任务生效, 不改变阶段二/三默认候选。
        """
        variants = list(super()._transfer_goal_variants(place, start_pose))
        if (self.unit_id == "A" and self.source == "agv"
                and self.park_tcp_pose is not None):
            pre = pre_place_pose(place)
            via = [float(self.park_tcp_pose[0]),
                   float(self.park_tcp_pose[1]), float(pre[2]),
                   *[float(v) for v in place[3:7]]]
            variants.append((
                "horizontal_detour_park",
                [Goal("detour_park", via, cartesian=True,
                      strict_orientation=True),
                 Goal("pre_place", pre, cartesian=True,
                      strict_orientation=True)],
                {"detour_offset": None,
                 "semantic_points": ["detour_park", "pre_place"]},
            ))
        return variants

    # ── 抬升规划 ────────────────────────────────────────────

    def _plan_lift(self, current_q, current_tcp):
        """LIFTING: 垂直直连抬升, 失败时使用既有局部回退.

        D 工位取料点在水平方向较远, 竖直笛卡尔抬升的关节插值偏差约
        6–8 mm, 被 CARTESIAN_PATH_POS_TOL=5 mm 拒绝; 改用同一 lift 位姿
        的关节目标 (cartesian=False), 仍检查关节限位/碰撞/分支/终点 TCP
        和 q0 锁. AGV 卸货点的 IK 可能落在镜像肩部解, 锁 q[0] 无解时
        解除锁定, 仍按垂直笛卡尔直线逐层做 IK/限位/碰撞/分支检查.
        不做未检查的关节直赋.
        """
        pick = contact_pose(self._pick_pose, self._pick_box, 0.0)
        lift = lift_pose(pick, self._place_pose()[3:7])
        goals = [Goal("lift", lift, cartesian=True,
                      lock_joint=self._lock_for("lift"))]
        context = MotionContext(
            phase="LIFT", target_handle=-1, carrying=True,
            snapshot=f"LIFT@t{self.sim_time:.2f}",
            allow_start_load_contact=True)
        plan = self.planner.plan(current_q, goals, context,
                                 start_pose=current_tcp,
                                 direct_only=True)
        if plan.ok:
            return plan
        if plan.status in (PlanStatus.GOAL_CONFIG_INVALID,
                           PlanStatus.PATH_DEVIATION) and \
                "法向插值偏差" in (plan.message or ""):
            joint_goals = [Goal("lift", lift, cartesian=False,
                                lock_joint=self._lock_for("lift"))]
            fallback = self.planner.plan(
                current_q, joint_goals, context,
                start_pose=current_tcp, direct_only=True)
            fallback.metrics.setdefault("lift_fallback", {
                "reason": "cartesian_deviation",
                "cartesian_status": plan.status.value,
                "cartesian_message": plan.message,
            })
            self._event(
                f"LIFT fallback joint goal: {plan.status.value} -> "
                f"{fallback.status.value}")
            return fallback
        if plan.status is PlanStatus.JOINT_LOCK_UNAVAILABLE:
            free_goals = [Goal("lift", lift, cartesian=True,
                               lock_joint=None)]
            fallback = self.planner.plan(
                current_q, free_goals, context,
                start_pose=current_tcp, direct_only=True)
            fallback.metrics.setdefault("lift_fallback", {
                "reason": "joint_lock_unavailable",
                "cartesian_status": plan.status.value,
                "cartesian_message": plan.message,
            })
            self._event(
                f"LIFT fallback unlocked q[0]: {plan.status.value} -> "
                f"{fallback.status.value}")
            return fallback
        return plan

    # ── 接近分支选择 (A 臂 AGV 取料) ──────────────────────────

    def _forced_pre_pick(self, current_q, current_tcp, pre_pose, pick_pose,
                         rec):
        """A 臂从 AGV 取料时预选能把后续抬升/转运走通的肩部解."""
        if not (self.unit_id == "A" and self.source == "agv"):
            return None
        return self._select_pre_pick_branch(
            current_q, current_tcp, pre_pose, pick_pose, rec)

    def _select_pre_pick_branch(self, current_q, current_tcp, pre_pose,
                                pick_pose, rec):
        """对少量 pre_pick IK 解各做一次完整链影子复核, 返回可用构型.

        实测 `staggered_batch`: 默认“最近构型”落在 q[0]≈-1.5 的肩部解,
        抬升后为肘上分支, 在 H_transfer 上无法把物料送到交接台 (直连
        横向偏差 7.8 mm; 28 个固定高度绕行点、beam 与 0.5 cm 步长均
        无解). 同一 pre_pick 位姿的 q[0]≈+2.0 肩部解抬升后 q[3]≈0,
        既有 `detour +0.30` 即可通过. 这里只强制完整链 (下探→抬升→
        水平转运) 已整体验证的构型; 每段仍走原有 IK/碰撞/分支/水平
        姿态复核, 不放宽任何容差. 先尝试另一侧肩部解, 其余候选按 IK
        顺序回退; 全部失败时返回 None, 保持原接近规划行为.
        """
        if self._pick_pose is None or self._pick_box is None:
            return None
        try:
            candidates = self.planner.solve_pose(pre_pose, current_q,
                                                 thorough=True)
        except Exception as exc:
            self._event(f"APPROACH branch lookahead skipped: {exc}")
            return None
        contact_ctx = MotionContext(
            phase="CONTACT",
            target_handle=(rec.sim_handle if rec is not None else -1),
            material_pose=list(self._pick_pose),
            material_box=self._pick_box, carrying=False,
            cup_radius=self.runtime.cup_radius,
            snapshot=f"LOOKAHEAD@t{self.sim_time:.2f}")
        usable: list[list[float]] = []
        for candidate in candidates:
            q = [float(v) for v in candidate]
            pos_err, ang_err = self.planner._pose_error(
                q, pre_pose, strict=True)
            if pos_err > s2cfg.EXEC_TCP_POS_TOL or \
                    ang_err > s2cfg.EXEC_TCP_ANG_TOL:
                continue
            check = self.checker.check_segment(
                current_q, q, contact_ctx, screen=True)
            if not check.ok:
                continue
            if all(joint_distance(q, seen) > 0.05 for seen in usable):
                usable.append(q)
        if not usable:
            return None
        usable.sort(key=lambda q: 0 if math.remainder(
            q[0], 2.0 * math.pi) > 0.0 else 1)
        usable = usable[:_APPROACH_BRANCH_MAX]
        attach = self.suction._relative_transform(
            list(pick_pose), list(self._pick_pose))
        self.runtime.set_attach_transform(attach)
        try:
            for q in usable:
                if self._chain_from_pre_pick(
                        q, pick_pose, contact_ctx):
                    self._event(
                        f"APPROACH branch selected q0={q[0]:.3f} "
                        f"q3={q[3]:.3f} from {len(usable)} candidates")
                    return q
        finally:
            self.runtime.set_attach_transform(None)
        self._event(
            f"APPROACH branch lookahead found no feasible chain "
            f"({len(usable)} candidates)")
        return None

    def _chain_from_pre_pick(self, q, pick_pose, contact_ctx) -> bool:
        """影子复核 pre_pick → 下探 → 抬升 → 水平转运 整链."""
        descend = self.planner.plan(
            q, [Goal("pick", pick_pose, cartesian=True,
                     allow_target_contact=True, final_contact=True)],
            contact_ctx, start_pose=self.checker.tcp_at(q),
            direct_only=True,
            wall_s=s2cfg.TRANSFER_PLAN_WALL_BUDGET_S)
        if not descend.ok:
            return False
        pick_q = descend.waypoints[-1]
        lift = self._plan_lift(pick_q, self.checker.tcp_at(pick_q))
        if lift is None or not lift.ok:
            return False
        lift_q = lift.waypoints[-1]
        lift_tcp = self.checker.tcp_at(lift_q)
        place = self._place_pose()
        transfer_ctx = MotionContext(
            phase="TRANSFER", target_handle=-1, carrying=True,
            snapshot=f"LOOKAHEAD-TRANSFER@t{self.sim_time:.2f}")
        for _name, goals, _info in self._transfer_goal_variants(
                place, lift_tcp):
            plan = self.planner.plan(
                lift_q, goals, transfer_ctx, start_pose=lift_tcp,
                direct_only=True,
                wall_s=s2cfg.TRANSFER_PLAN_WALL_BUDGET_S)
            if not plan.ok:
                continue
            check = horizontal_transfer_check(
                plan.waypoints, self.checker, orientation=place[3:7])
            if check.get("query_unknown") or not check.get("ok"):
                continue
            return True
        return False

    def _build_plan(self):
        """阶段四覆盖: LIFTING 走 :meth:`_plan_lift`, 交接台放置后撤离到 park.

        LIFTING 固定为垂直直连抬升 (吸附点仍在取料面上, 起点允许载荷
        接触; 其余检查与 06-15 的 direct_only 转运一致). A 臂在共享
        交接台放置后必须撤离到布局派生的 park 位姿, 给 C 臂让出交接台
        包络; 其余状态沿用阶段二/三规划.
        """
        if self.state is UnitTaskState.LIFTING:
            try:
                current_q, current_tcp, start_source = self._planning_start()
            except Exception as exc:
                self._fail(f"POSE_UNREADABLE: {exc}")
                return None
            if self._pick_pose is None or self._pick_box is None:
                self._fail("PICK_TARGET_MISSING")
                return None
            self._event(
                f"LIFT START {start_source} "
                f"q={[round(v, 4) for v in current_q]}")
            return self._plan_lift(current_q, current_tcp)
        if (self.state is Stage2TaskState.RETREATING
                and self.park_tcp_pose is not None):
            try:
                current_q, current_tcp, start_source = self._planning_start()
            except Exception as exc:
                self._fail(f"POSE_UNREADABLE: {exc}")
                return None
            self._event(
                f"PARK START {start_source} "
                f"q={[round(v, 4) for v in current_q]}")
            goals = [Goal("park", list(self.park_tcp_pose), cartesian=True,
                          allow_target_contact=True)]
            context = MotionContext(
                phase="RETREAT", target_handle=-1, carrying=False,
                snapshot=f"PARK@t{self.sim_time:.2f}")
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp)
        return super()._build_plan()

    def _placing_target_ready(self) -> bool:
        if self.sink == "agv":
            docked = self.scene.agv_dock_hit(self.scene.agv_root)
            stopped = self._agv_stationary() and self._agv_upright()
            if docked and stopped:
                self._dock_wait_since = None
                return True
            if self._dock_wait_since is None:
                self._dock_wait_since = self.sim_time
            elif (self.sim_time - self._dock_wait_since
                  > base.AGV_DOCK_TIMEOUT):
                self._fail(
                    f"AGV_DOCK_TIMEOUT: 下探前 AGV 未就绪 "
                    f"(dock_sensor={docked}, stopped={stopped})")
            return False

        if self.sink == "conveyor":
            if self.line_fault:
                self._fail("CONVEYOR_FAULT: 放料线已被故障停带")
                return False
            self.conveyor.stop(self.line.conveyor)
            speed = read_conveyor_speed(
                self.sim, self.scene.handle(self.line.conveyor),
                base.CONVEYOR_STATE_KEY)
            still = False
            if speed.valid and abs(float(speed.value or 0.0)) <= \
                    cfg.LINE_STOP_VEL_TOL:
                still = True
            elif not speed.valid:
                pad = self._pad_position()
                still = (pad is not None and self._latch_pad_prev is not None
                         and math.dist(pad, self._latch_pad_prev)
                         <= s2cfg.CONVEYOR_STOP_POS_TOL)
                self._latch_pad_prev = pad
            if still:
                self._line_stop_count += 1
            else:
                self._line_stop_count = 0
            if self._line_stop_count >= cfg.LINE_STOP_CONFIRM_STEPS:
                return True
            if self._line_stop_since is None:
                self._line_stop_since = self.sim_time
            elif (self.sim_time - self._line_stop_since
                  > cfg.LINE_STOP_TIMEOUT):
                self._fail(
                    "SINK_CONVEYOR_NOT_STOPPED: 放料前带面未停止 "
                    f"(speed={speed.value}, valid={speed.valid})")
            return False

        return True

    # ── 线/安全 ─────────────────────────────────────────────

    def _stop_line(self) -> bool:
        try:
            if self.line_fault:
                return True
            self.conveyor.stop(self.line.conveyor)
            return True
        except Exception:
            return False

    def _hold_after_latch(self) -> None:
        try:
            actual = self.ik.joint_positions()
            self.ik.apply_joints(actual, motion=_motion_params())
        except Exception:
            pass
        self._stop_line()
        if not self._hold_logged:
            self._hold_logged = True
            self._event("SAFETY_HOLD: 已取消剩余轨迹并保持最后实测构型")

    def _safety_fail(self, reason: str,
                     details: Optional[dict] = None) -> None:
        if self.safety.engaged:
            return
        self._capture_motion_report(f"{s2cfg.SAFETY_LATCH_CODE}: {reason}")
        self.safety.request_stop(reason, sim_time=self.sim_time,
                                 details=details or {})
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        ok = self._stop_line()
        self.safety.note_stop_command(ok, sim_time=self.sim_time)
        self._hold_after_latch()
        self._fail(f"{s2cfg.SAFETY_LATCH_CODE}: {reason}")

    def _confirm_latch_stop(self) -> None:
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(self.line.conveyor),
            base.CONVEYOR_STATE_KEY)
        agv_stopped = self._agv_stationary() if self.agv is not None else True
        if speed.valid:
            confirmed = self.safety.confirm_stop(
                speed_known=True, speed=speed.value,
                agv_stopped=agv_stopped, source=speed.source)
        else:
            pad = self._pad_position()
            pad_still = (pad is not None and self._latch_pad_prev is not None
                         and math.dist(pad, self._latch_pad_prev)
                         <= s2cfg.CONVEYOR_STOP_POS_TOL)
            self._latch_pad_prev = pad
            confirmed = self.safety.confirm_stop_from_position(
                pad_still=bool(pad_still), agv_stopped=agv_stopped)
        if confirmed and not self._stop_confirmed_logged:
            self._stop_confirmed_logged = True
            self._event(
                f"SAFETY_STOP_CONFIRMED: speed={self.safety.latch.stop_speed} "
                f"source={self.safety.latch.stop_source}")

    def _fail(self, reason: str) -> None:
        if self.state is UnitTaskState.FAILED:
            return
        self._capture_motion_report(reason)
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        self._stop_line()
        self._retry_at = None
        self.state = UnitTaskState.FAILED
        self.failure_reason = reason
        self._event(f"FAILED: {reason}")
        if self.logger:
            self.logger.warning("[V1][%s] uid=%s 任务失败: %s",
                                self.unit_id, self.uid, reason)

    # ── 板载工具 ────────────────────────────────────────────

    def _update_agv(self) -> None:
        """阶段四 AGV 由协调器航线独占, 单岛控制器绝不 set_cmd/stop."""
        return None

    def summary(self) -> dict:
        data = super().summary()
        data.update({
            "controller_id": cfg.STAGE4_CONTROLLER_ID,
            "phase": cfg.STAGE4_PHASE,
            "line": self.line.line_id,
            "line_fault": bool(self.line_fault),
            "expected_types": [item.value for item in self.expected_types],
            "sink_kind": self.sink,
            "sink_zone": self.zone,
            "flow": (self.flow.by_uid(self.uid).as_dict()
                     if self.flow.by_uid(self.uid) is not None else None),
        })
        return data
