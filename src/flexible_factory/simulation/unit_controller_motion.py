"""unit_controller_motion.py — v1 运动层单步闭环.

继承 :class:`flexible_factory.simulation.unit_controller_base.Stage1UnitController`,
保持原有状态机与 ``sim.step()`` 永远最后的顺序, 但替换三处核心行为:

  1. 运动: ``_run_motion`` 不再用单次 IK + 关键帧, 而是
     ``LocalPlanner`` 生成完整 ``MotionPlan`` → 每步仅提交经
     ``check_segment`` 复核的下一构型;
  2. 吸附/释放: 使用运动层 ``Stage2SuctionController`` 的外表面接触判据
     与保持世界位姿的释放解绑;
  3. 安全: 每步前后检查真实 q/TCP/载荷与命令的一致性, 风险/未知读数立即
     ``request_stop`` 锁存, 取消剩余轨迹, 停带/停车, 保持最后实测构型;
     锁存后不自动恢复、不重试。

停带改用 ``ConveyorStopMonitor``: 速度回读不可用时不再返回 0.0, 必须由
独立 pad 位移连续窗口确认, 否则锁存。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from . import config_base as base
from .conveyor import (BeltGeometry, TransportFault,
                                        compensation_allowed)
from .material_manager import MaterialState
from .unit_controller_base import (MismatchReason, PickMismatch,
                                    Stage1UnitController, UnitTaskState,
                                    quat_tilt)

from . import config_motion as cfg
from .motion.branch import branch_ok
from .motion.contact import (LocalBox, contact_pose, quat_angle_between,
                      quat_multiply, quat_rotate)
from .motion.conveyor_safety import (ConveyorStopMonitor, MaterialStabilityTracker,
                              StopStatus, bounded_transport_step,
                              read_conveyor_speed)
from .motion.path_planning import (Goal, MotionContext, PlanStatus,
                            ConfigurationChecker, MotionPlan,
                            failure_priority)
from .motion.safety import SafetyObservation
from .motion.trajectory import ExecutorStatus, JointStreamExecutor, joint_distance


class Stage2TaskState(Enum):
    RETREATING = auto()


@dataclass(frozen=True)
class ConveyorPickContext:
    """一次输送带取料的线级参数.

    运动层默认沿用基础层单线配置; 双站层保持默认, v1 按实际物流线覆盖
    别名、几何、带速、抓取区与传感器. 停带/稳定测量仍由
    :mod:`motion.conveyor_safety` 的监视器完成.
    """

    conveyor_alias: str
    belt_geometry: BeltGeometry
    belt_speed: float
    pick_region: base.PickRegion
    sensor_alias: Optional[str] = None
    label: str = ""


class Stage2UnitController(Stage1UnitController):
    def __init__(self, *args, runtime, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.runtime = runtime
        self.safety = runtime.safety
        self.checker: ConfigurationChecker = runtime.checker
        self.planner = runtime.planner
        self.probe = runtime.probe
        self.collisions = runtime.collisions

        self._plan: Optional[MotionPlan] = None
        self._last_plan: Optional[MotionPlan] = None
        self._executor: Optional[JointStreamExecutor] = None
        self._pick_box: Optional[LocalBox] = None
        self._release_pose: Optional[list[float]] = None
        self._stop_monitor: Optional[ConveyorStopMonitor] = None
        self._stability: Optional[MaterialStabilityTracker] = None
        self._material_prev_pos: Optional[list[float]] = None
        self._compensation_steps = 0
        self._compensation_consecutive = 0
        self._overspeed_steps = 0
        self._long_compensation = False
        self._latch_pad_prev: Optional[list[float]] = None
        self._stop_confirmed_logged = False
        self._hold_logged = False
        self._last_fk_report: Optional[dict] = None
        # 阶段 2.01: 腕部分支锚点与逐段运动证据
        self._branch_anchor: Optional[str] = None
        self._motion_reports: list[dict] = []
        self._motion_reported = False
        # 上一段已执行计划的终点构型: 实测在其容差内时作为下一段规划起点,
        # 保证 A/B 与复跑使用同一冻结起点; 超出则退回实测并记录.
        self._planned_end_q: Optional[list[float]] = None
        # 06-15: PLACING 是独立下探运动; 下探前先确认 pre_place 与目标设备
        # 条件, 确认后同一状态内规划并执行 pre_place → place.
        self._placing_ready = False
        self._transfer_check: Optional[dict] = None

    # ═══════════════════════════════════════════════════════
    # 单步顺序: 联锁 → 传感器/传送带 → 任务 → AGV → 日志 → step
    # ═══════════════════════════════════════════════════════

    def step(self) -> None:
        """阶段二单控制器循环: 联锁/任务 + 推进物理 + 步后联锁."""
        self.pre_tick()
        self.sim.step()
        self.post_tick()

    def pre_tick(self) -> None:
        """本控制步的联锁/传感器/任务逻辑, 不推进物理引擎.

        阶段三协调器对两台控制器依次调用 `pre_tick()`, 再统一下发 AGV
        轮速并 `sim.step()`, 最后调用 `post_tick()`; 阶段二单控制器路径
        `step()` 的顺序与结果保持不变.
        """
        self.sim_time = self._sim_time()
        if self._started_at is None:
            self._started_at = self.sim_time
        self._probe_comm()
        if self.safety.engaged or self.state is UnitTaskState.FAILED:
            self._hold_after_latch()
        else:
            self._pre_step_interlock()
            if not self.safety.engaged:
                self._update_conveyor()
                self._read_pick_sensor()
                self._advance_task()
                self._update_agv()
        self._log_step()

    def post_tick(self) -> None:
        self._post_step_interlock()

    # ═══════════════════════════════════════════════════════
    # 联锁
    # ═══════════════════════════════════════════════════════

    def _pre_step_interlock(self) -> None:
        if self.state in (UnitTaskState.IDLE, UnitTaskState.WAITING_FOR_ITEM,
                          UnitTaskState.STOPPING,
                          UnitTaskState.MATERIAL_SETTLING):
            return
        try:
            actual = self.ik.joint_positions()
            tcp = self.ik.tcp_pose()
        except Exception as exc:
            self._safety_fail(f"POSE_UNREADABLE: {exc}")
            return
        if self._branch_anchor is not None:
            ok, label, margin = branch_ok(
                actual, self._branch_anchor, cfg.WRIST_BRANCH_MARGIN)
            if not ok:
                self._safety_fail(
                    f"WRIST_BRANCH_VIOLATION: 实测 q4 分支 {label} "
                    f"(裕量 {margin:.3f} rad) 偏离锚点 "
                    f"{self._branch_anchor}")
                return
        commanded = self._motion_cmd
        expected_tcp = None
        moving = False
        if commanded is not None:
            expected_tcp = self.checker.tcp_at(commanded)
            moving = joint_distance(actual, commanded) > \
                2.0 * cfg.EXEC_JOINT_TOL
        rec = self.materials.by_uid(self.uid)
        attached = (rec is not None
                    and rec.state is MaterialState.HELD_BY_SUCTION)
        material_pose = material_expected = None
        if attached and rec is not None and rec.sim_handle is not None:
            try:
                material_pose = self.sim.getObjectPose(
                    rec.sim_handle, self.sim.handle_world)
            except Exception:
                material_pose = None
            transform = self.suction.attach_transform
            if transform is not None and tcp is not None:
                material_expected = _compose(tcp, transform)
        observation = SafetyObservation(
            sim_time=self.sim_time, phase=self.state.name,
            actual_q=actual, commanded_q=commanded, tcp_pose=tcp,
            expected_tcp=expected_tcp, material_pose=material_pose,
            material_expected=material_expected, attached=attached,
            details={"moving": moving})
        violations = self.safety.evaluate(observation)
        if violations:
            self._safety_fail("; ".join(violations), observation.as_dict())

    def _post_step_interlock(self) -> None:
        if self.safety.engaged:
            self._confirm_latch_stop()
            return
        if self._motion_cmd is None:
            return
        try:
            actual = self.ik.joint_positions()
        except Exception as exc:
            self._safety_fail(f"JOINT_READBACK_FAILED: {exc}")
            return
        deviation = joint_distance(actual, self._motion_cmd)
        if deviation > cfg.EXEC_MAX_DEVIATION:
            self._safety_fail(
                f"JOINT_DEVIATION: {deviation:.4f} rad > "
                f"{cfg.EXEC_MAX_DEVIATION}")

    def _hold_after_latch(self) -> None:
        """保持最后实测构型, 停带/停车; 不再下发任何运动目标."""
        try:
            actual = self.ik.joint_positions()
            self.ik.apply_joints(actual, motion=_motion_params())
        except Exception:
            pass
        try:
            self.conveyor.stop(base.ALIASES["conveyor"])
        except Exception:
            pass
        if self.agv is not None:
            try:
                self.agv.stop()
            except Exception:
                pass
        if not self._hold_logged:
            self._hold_logged = True
            self._event("SAFETY_HOLD: 已取消剩余轨迹并保持最后实测构型")

    def _safety_fail(self, reason: str, details: Optional[dict] = None) -> None:
        if self.safety.engaged:
            return
        # 先保留当前段实测证据, 再取消轨迹并清空执行器.
        self._capture_motion_report(f"{cfg.SAFETY_LATCH_CODE}: {reason}")
        self.safety.request_stop(reason, sim_time=self.sim_time,
                                 details=details or {})
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        ok = True
        try:
            self.conveyor.stop(base.ALIASES["conveyor"])
        except Exception:
            ok = False
        if self.agv is not None:
            try:
                self.agv.stop()
            except Exception:
                ok = False
        self.safety.note_stop_command(ok, sim_time=self.sim_time)
        self._hold_after_latch()
        self._fail(f"{cfg.SAFETY_LATCH_CODE}: {reason}")

    def _confirm_latch_stop(self) -> None:
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(base.ALIASES["conveyor"]),
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
                         <= cfg.CONVEYOR_STOP_POS_TOL)
            self._latch_pad_prev = pad
            confirmed = self.safety.confirm_stop_from_position(
                pad_still=bool(pad_still), agv_stopped=agv_stopped)
        if confirmed and not self._stop_confirmed_logged:
            self._stop_confirmed_logged = True
            self._event(
                f"SAFETY_STOP_CONFIRMED: speed={self.safety.latch.stop_speed} "
                f"source={self.safety.latch.stop_source}")

    # ═══════════════════════════════════════════════════════
    # 任务推进
    # ═══════════════════════════════════════════════════════

    def _advance_task(self) -> None:
        if self.safety.engaged:
            return
        if self.state is Stage2TaskState.RETREATING:
            self._run_motion()
            return
        if self.state is UnitTaskState.PLACING:
            self._advance_placing()
            return
        if self.state is UnitTaskState.ATTACHING:
            check = self._verify_attach_configuration()
            if check is not None and not check.ok:
                if check.status is PlanStatus.QUERY_UNKNOWN:
                    self._safety_fail(
                        f"ATTACH_QUERY_UNKNOWN: {check.message}")
                else:
                    self._fail(f"ATTACH_CONFIG_INVALID: {check.message}")
                return
        super()._advance_task()

    def _verify_attach_configuration(self):
        rec = self.materials.by_uid(self.uid)
        if rec is None or self._pick_pose is None or self._pick_box is None:
            return None
        context = MotionContext(
            phase="CONTACT", target_handle=rec.sim_handle,
            material_pose=self._pick_pose, material_box=self._pick_box,
            carrying=False, snapshot=f"attach@t{self.sim_time:.2f}",
            branch=self._branch_anchor)
        try:
            return self.checker.check(
                self.ik.joint_positions(), context,
                allow_target_contact=True, final_contact=True, fast=True)
        except Exception as exc:
            from .motion.path_planning import ConfigCheck
            return ConfigCheck(PlanStatus.QUERY_UNKNOWN,
                               list(self.ik.joint_positions()),
                               message=f"{type(exc).__name__}: {exc}")

    def _after_settling_checks(self) -> bool:
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return False
        try:
            actual = list(self.sim.getObjectPosition(
                rec.sim_handle, self.sim.handle_world))
        except Exception as exc:
            self._fail(f"RELEASE_POSE_UNREADABLE: {exc}")
            return False
        self._release_pose = (getattr(self.suction, "release_pose", None)
                              or actual)
        reference = self._release_pose or actual
        drift = math.dist(actual, reference[:3])
        if drift > cfg.RELEASE_SLIP_TOL:
            self._fail(
                f"RELEASE_SLIP: 稳定窗口内载荷漂移 {drift:.4f} m > "
                f"{cfg.RELEASE_SLIP_TOL} m")
            return False
        self.runtime.update_materials(self.materials, self.uid,
                                      carrying=False)
        self.runtime.set_attach_transform(None)
        self._plan = None
        self._executor = None
        self._motion_started = False
        self._motion_cmd = None
        self._event(f"RETREATING: 抬升撤离 (drift={drift:.4f} m)")
        return True

    def _settling_complete_state(self):
        """阶段二在稳定窗口后先撤离; 漂移超限时保持 FAILED."""
        if not self._after_settling_checks():
            return UnitTaskState.DONE
        return Stage2TaskState.RETREATING

    # ═══════════════════════════════════════════════════════
    # 放置前下探 (06-15)
    # ═══════════════════════════════════════════════════════

    def _advance_placing(self) -> None:
        """PLACING: 先确认目标设备, 再执行 pre_place → place 法向下探."""
        if not self._placing_ready:
            if not self._placing_target_ready():
                return
            self._placing_ready = True
            try:
                tcp = [float(v) for v in self.ik.tcp_pose()]
            except Exception:
                tcp = None
            self._event(
                "PLACING: 下探许可 (target=%s, tcp=%s)" % (
                    self.target,
                    None if tcp is None else [round(v, 4) for v in tcp[:3]]))
        self._run_motion()

    def _placing_target_ready(self) -> bool:
        """下探前条件: AGV 到站/停稳/水平; 未满足时等待, 超时失败.

        检查先于任何下探命令; 失败时不生成 pre_place → place 计划,
        保持既有 ``AGV_DOCK_TIMEOUT`` 行为.
        """
        if self.target != "agv":
            return True
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
                f"(dock_sensor={docked}, stopped={stopped}); "
                "未生成 pre_place → place 命令")
            return False
        return False

    # ═══════════════════════════════════════════════════════
    # 运动
    # ═══════════════════════════════════════════════════════

    def _begin_motion(self, state: UnitTaskState, reason: str = "") -> None:
        if state is UnitTaskState.APPROACHING and self._pick_tcp is None:
            self._fail("PICK_TARGET_MISSING: 未读取到实测抓取目标")
            return
        self.state = state
        self._plan = None
        self._executor = None
        self._motion_started = False
        self._motion_pose = None
        self._motion_cmd = None
        self._motion_reported = False
        self._placing_ready = False
        self._dock_wait_since = None
        rec = self.materials.by_uid(self.uid)
        if state is UnitTaskState.APPROACHING:
            self.runtime.update_materials(self.materials, self.uid,
                                          carrying=False)
            self.runtime.set_attach_transform(None)
        elif state in (UnitTaskState.LIFTING, UnitTaskState.TRANSFERRING,
                       UnitTaskState.PLACING):
            self.runtime.update_materials(self.materials, self.uid,
                                          carrying=True)
            self.runtime.set_attach_transform(
                getattr(self.suction, "attach_transform", None))
        elif state is Stage2TaskState.RETREATING:
            self.runtime.update_materials(self.materials, self.uid,
                                          carrying=False)
            self.runtime.set_attach_transform(None)
        self._event(f"{state.name}: {reason or 'stage2 motion started'}")

    def _run_motion(self) -> None:
        if self._plan is None:
            plan = self._build_plan()
            if plan is None:
                return
            self._last_plan = plan
            if not plan.ok:
                self._event(
                    f"PLAN FAILED {plan.status.value}: {plan.message}")
                self._motion_reports.append({
                    "state": self.state.name,
                    "status": plan.status.value,
                    "message": plan.message,
                    "attempts": plan.attempts,
                    "branch_anchor": plan.branch_anchor,
                })
                self._motion_reported = True
                if plan.status is PlanStatus.QUERY_UNKNOWN:
                    self._safety_fail(f"PLAN_QUERY_UNKNOWN: {plan.message}",
                                      plan.as_dict())
                else:
                    self._fail(f"{plan.status.value}: {plan.message}")
                return
            self._branch_anchor = plan.branch_anchor
            totals = (plan.metrics or {}).get("totals", {})
            self._event(
                f"PLAN {plan.status.value} strategy={plan.strategy} "
                f"policy={plan.lock_policy} segments={len(plan.segments)} "
                f"samples={plan.samples} points={totals.get('points')} "
                f"tv={totals.get('tv_total')} "
                f"wall={plan.wall_s:.2f}s")
            self._plan = plan
            # 规划预算只约束本次规划/验证; 执行期由执行器的偏差/超时复核
            # 守护, 不继承规划墙钟 (并发运行时长轨迹会被误判为规划超限).
            self.checker.set_budget(self.planner.budget)
            self._executor = JointStreamExecutor(
                plan, self.checker, plan.contexts,
                pose_at=self.checker.tcp_at)
            self._motion_started = True
            self._motion_started_at = self.sim_time
            return

        assert self._executor is not None
        step = self._executor.next_command(self.ik.joint_positions(),
                                           self.sim_time)
        if step.status in (ExecutorStatus.COLLISION, ExecutorStatus.UNKNOWN,
                           ExecutorStatus.DEVIATION, ExecutorStatus.TIMEOUT,
                           ExecutorStatus.WRIST_BRANCH):
            self._safety_fail(f"{step.status.value}: {step.message}",
                              step.check)
            return
        if step.done:
            final = self._executor.final_waypoint
            actual = self.ik.joint_positions()
            if joint_distance(actual, final) > 2.0 * cfg.EXEC_JOINT_TOL:
                self._safety_fail(
                    "GOAL_NOT_REACHED",
                    {"actual": actual, "planned": final})
                return
            plan = self._last_plan
            goal_pose = plan.goal_pose if plan is not None else None
            if goal_pose is not None:
                if plan.strict_orientation:
                    reached = self.ik.reached(goal_pose)
                else:
                    pos_err, ang_err = self.planner._pose_error(
                        actual, goal_pose, strict=False)
                    reached = (pos_err <= cfg.EXEC_TCP_POS_TOL
                               and ang_err <= cfg.EXEC_TCP_ANG_TOL)
                if not reached:
                    self._fail("GOAL_CONFIG_INVALID: TCP 未到达计划目标")
                    return
            execution = self._executor.execution_metrics()
            self._planned_end_q = [float(v) for v in final]
            self._event(
                f"MOTION {self.state.name} executed "
                f"tv={execution['totals'].get('tv_total')} "
                f"max_step={execution['totals'].get('max_step')} "
                f"steps={execution['totals'].get('steps')} "
                f"time={execution['totals'].get('sim_duration')}s")
            self._motion_reports.append({
                "state": self.state.name,
                "status": "DONE",
                "branch_anchor": self._branch_anchor,
                "plan": plan.summary_dict() if plan is not None else None,
                "execution": execution,
                "tcp_pose": [round(float(v), 6) for v in
                             self.ik.tcp_pose()],
            })
            self._motion_reported = True
            self._executor = None
            self._plan = None
            self._motion_started = False
            self._motion_pose = None
            self._motion_cmd = list(actual)
            if not self._verify_fk_at_actual():
                return
            self._finish_motion_state()
            return
        if (self._motion_cmd is None
                or joint_distance(step.q, self._motion_cmd) > 1e-6):
            # 同一目标不重复下发: 4.10 的 kinematic 关节目标带运动规划,
            # 每步重发会反复重置加速段, 关节永远追不上目标.
            self.ik.apply_joints(step.q, motion=_motion_params())
            self._motion_cmd = list(step.q)

    def _verify_fk_at_actual(self) -> bool:
        """在真实臂当前构型比较真实/影子 FK; 不一致立即锁存."""
        try:
            actual = self.ik.joint_positions()
            self.probe.set_configuration(actual)
            report = self.probe.verify_fk(self.scene)
        except Exception as exc:
            self._safety_fail(f"FK_CHECK_FAILED: {type(exc).__name__}: {exc}")
            return False
        self._last_fk_report = report
        if not report.get("ok"):
            self._safety_fail("FK_MISMATCH", report)
            return False
        return True

    def _finish_motion_state(self) -> None:
        state = self.state
        if state is UnitTaskState.APPROACHING:
            self._enter(UnitTaskState.ATTACHING, "approach complete")
        elif state is UnitTaskState.LIFTING:
            self._begin_motion(UnitTaskState.TRANSFERRING,
                               reason="lift complete")
        elif state is UnitTaskState.TRANSFERRING:
            self._begin_motion(UnitTaskState.PLACING,
                               reason="transfer complete")
        elif state is UnitTaskState.PLACING:
            self._enter(UnitTaskState.RELEASING, "descent complete")
        elif state is Stage2TaskState.RETREATING:
            self._enter(UnitTaskState.DONE, "retreat complete")
        else:
            self._fail(f"UNKNOWN_MOTION_STATE: {state}")

    @staticmethod
    def _lock_for(name: str):
        """阶段 2.01 段内固定 q[0] 策略 (值在规划时取段起始构型)."""
        return lock_for(name)

    def _planning_start(self) -> tuple[list[float], list[float], str]:
        """选择规划起始构型: 优先上一段计划终点, 偏差超限退回实测."""
        actual = [float(v) for v in self.ik.joint_positions()]
        actual_tcp = list(self.ik.tcp_pose())
        if self._planned_end_q is None:
            return actual, actual_tcp, "actual"
        planned = [float(v) for v in self._planned_end_q]
        deviation = joint_distance(actual, planned)
        if deviation <= 2.0 * cfg.EXEC_JOINT_TOL:
            start_tcp = self.checker.tcp_at(planned) or actual_tcp
            return planned, list(start_tcp), f"planned_end(dev={deviation:.4f})"
        self._event(
            f"PLAN_START_ACTUAL: 实测 q 偏离上一计划终点 "
            f"{deviation:.4f} rad > {2.0 * cfg.EXEC_JOINT_TOL:.4f}")
        return actual, actual_tcp, "actual"

    def _forced_pre_pick(self, current_q, current_tcp, pre_pose, pick_pose,
                         rec) -> Optional[list[float]]:
        """可覆盖: 指定 pre_pick 的 IK 构型 (默认返回 None, 自由搜索).

        阶段四 A 臂 AGV 取料用它选择能把后续抬升/转运走通的肩部解;
        覆盖方必须先完成该构型的下游链影子复核.
        """
        return None

    def _build_plan(self) -> Optional[MotionPlan]:
        rec = self.materials.by_uid(self.uid)
        try:
            current_q, current_tcp, start_source = self._planning_start()
        except Exception as exc:
            self._fail(f"POSE_UNREADABLE: {exc}")
            return None
        snapshot = f"{self.state.name}@t{self.sim_time:.2f}"
        self._event(f"PLAN START {start_source} q={[round(v, 4) for v in current_q]}")
        state = self.state

        if state is UnitTaskState.APPROACHING:
            if self._pick_pose is None or self._pick_box is None:
                self._fail("PICK_TARGET_MISSING")
                return None
            pre = contact_pose(self._pick_pose, self._pick_box,
                               cfg.PRE_APPROACH_CLEARANCE)
            pick = contact_pose(self._pick_pose, self._pick_box, 0.0)
            self._pick_tcp = list(pick)
            forced = self._forced_pre_pick(current_q, current_tcp, pre, pick,
                                           rec)
            goals = [Goal("pre_pick", pre, forced_q=forced),
                     Goal("pick", pick, cartesian=True,
                          allow_target_contact=True, final_contact=True)]
            context = MotionContext(
                phase="CONTACT",
                target_handle=(rec.sim_handle if rec is not None else -1),
                material_pose=list(self._pick_pose),
                material_box=self._pick_box, carrying=False,
                cup_radius=self.runtime.cup_radius, snapshot=snapshot)
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp)

        if state is UnitTaskState.LIFTING:
            if self._pick_pose is None or self._pick_box is None:
                self._fail("PICK_TARGET_MISSING")
                return None
            # 06-15: 吸附抬升直接到达共享转运高度 H_transfer; 工具姿态与
            # pre_place 一致, 使 TRANSFERRING 全程只做水平平移.
            pick = contact_pose(self._pick_pose, self._pick_box, 0.0)
            lift = lift_pose(pick, self._place_pose()[3:7])
            goals = [Goal("lift", lift, cartesian=True,
                          lock_joint=self._lock_for("lift"))]
            context = MotionContext(phase="LIFT", target_handle=-1,
                                    carrying=True, snapshot=snapshot,
                                    allow_start_load_contact=True)
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp)

        if state is UnitTaskState.TRANSFERRING:
            return self._plan_transfer(current_q, current_tcp, snapshot)

        if state is UnitTaskState.PLACING:
            # 06-15: PLACING 是独立的下探运动 pre_place → place.
            place = self._place_pose()
            goals = [Goal("place", place, cartesian=True,
                          strict_orientation=True,
                          allow_final_load_contact=True,
                          lock_joint=self._lock_for("place"))]
            context = MotionContext(phase="PLACE", target_handle=-1,
                                    carrying=True, snapshot=snapshot)
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp,
                                     direct_only=True,
                                     wall_s=cfg.TRANSFER_PLAN_WALL_BUDGET_S)

        if state is Stage2TaskState.RETREATING:
            retreat = list(current_tcp)
            retreat[2] += cfg.PRE_APPROACH_CLEARANCE
            goals = [Goal("retreat", retreat, cartesian=True,
                          allow_target_contact=True,
                          lock_joint=self._lock_for("retreat"))]
            material_pose = None
            if rec is not None and rec.sim_handle is not None:
                try:
                    material_pose = list(self.sim.getObjectPose(
                        rec.sim_handle, self.sim.handle_world))
                except Exception:
                    material_pose = None
            context = MotionContext(
                phase="RETREAT",
                target_handle=(rec.sim_handle if rec is not None else -1),
                material_pose=material_pose, material_box=self._pick_box,
                carrying=False, cup_radius=self.runtime.cup_radius,
                snapshot=snapshot)
            return self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp)

        self._fail(f"NO_PLAN_FOR_STATE: {state}")
        return None

    def _transfer_goal_variants(self, place, start_pose
                                ) -> list[tuple[str, list[Goal], dict]]:
        """TRANSFERRING 候选; 阶段四可追加同高度局部绕行 (默认不变)."""
        return transfer_goal_variants(place, start_pose)

    def _plan_transfer(self, current_q, current_tcp,
                       snapshot: str) -> MotionPlan:
        """06-15: TRANSFERRING 把 TCP 固定高度/姿态带到 pre_place.

        目标由放置点与共享高度 ``TRANSFER_HEIGHT`` 固定; 先尝试水平直线,
        直线被自身运动学奇异/分支拒绝时, 只允许一个固定高度的横向绕行点
        (两段笛卡尔水平线). 所有候选都以 ``direct_only=True`` 规划, 不走
        HOME/抬高中转等关节路径. 计划完成后逐 waypoint 复核水平高度与
        工具姿态.
        """
        place = self._place_pose()
        context = MotionContext(phase="TRANSFER", target_handle=-1,
                                carrying=True, snapshot=snapshot)
        self._transfer_check = None
        reports: list[dict] = []
        best: Optional[MotionPlan] = None
        for name, goals, info in self._transfer_goal_variants(
                place, current_tcp):
            plan = self.planner.plan(current_q, goals, context,
                                     start_pose=current_tcp,
                                     direct_only=True,
                                     wall_s=cfg.TRANSFER_PLAN_WALL_BUDGET_S)
            plan.transfer_path = name
            check = None
            if plan.ok:
                check = horizontal_transfer_check(
                    plan.waypoints, self.checker, orientation=place[3:7])
                plan.metrics.setdefault("horizontal_check", check)
                self._transfer_check = check
                if check.get("query_unknown"):
                    plan.status = PlanStatus.QUERY_UNKNOWN
                    plan.message = ("水平转运路径复核查询未知: "
                                    f"{check.get('error')}")
                elif not check["ok"]:
                    plan.status = PlanStatus.PATH_DEVIATION
                    plan.message = (
                        f"水平转运路径偏差 z={check['max_z']:.4f} m "
                        f"ang={check['max_ang']:.4f} rad > "
                        f"{cfg.EXEC_TCP_POS_TOL} m / "
                        f"{cfg.EXEC_TCP_ANG_TOL} rad")
            totals = (plan.metrics or {}).get("totals", {})
            row = {
                "variant": name,
                "info": info,
                "status": plan.status.value,
                "message": plan.message,
                "strategy": plan.strategy,
                "tv_total": totals.get("tv_total"),
                "points": totals.get("points"),
                "horizontal_ok": None if check is None else check["ok"],
                "max_z": None if check is None else check.get("max_z"),
                "max_ang": None if check is None else check.get("max_ang"),
            }
            self._event(
                f"TRANSFER {name}: {plan.status.value} "
                f"strategy={plan.strategy} tv={totals.get('tv_total')} "
                f"wall={plan.wall_s:.2f}s {plan.message}")
            if plan.status is PlanStatus.QUERY_UNKNOWN:
                # 未知读数按现有安全锁存契约处理: 不再尝试其他变体.
                reports.append(row)
                plan.transfer_variants = reports
                return plan
            if plan.ok:
                plan.transfer_variants = reports + [row]
                return plan
            reports.append(row)
            if best is None or failure_priority(plan.status) > \
                    failure_priority(best.status):
                best = plan
        if best is not None:
            best.transfer_variants = reports
        return best

    # ═══════════════════════════════════════════════════════
    # 传送带 / 停带 / 物料稳定
    # ═══════════════════════════════════════════════════════

    def _pick_context(self) -> ConveyorPickContext:
        """输送带取料的线级参数; 双站层沿用基础层单线配置."""
        return ConveyorPickContext(
            conveyor_alias=self._conveyor_pick_alias(),
            belt_geometry=self.belt_geometry,
            belt_speed=base.CONVEYOR_SPEED,
            pick_region=base.ALLOWED_PICK_REGION)

    def _pick_sensor_hits(self) -> list[int]:
        """当前取料传感器命中的物料句柄.

        默认使用基础层共享取料传感器; 有独立线传感器的层按别名过滤.
        """
        ctx = self._pick_context()
        if ctx.sensor_alias is None:
            return list(self.scene.pick_sensor_hits(self.materials.handles()))
        return [h for h in self.materials.handles()
                if self.scene.check_sensor(ctx.sensor_alias, h) is not None]

    def _check_pick_candidate(self, rec, pose) -> bool:
        """停稳物料的阶段额外准入校验; 返回 True 表示已记录失败.

        默认无额外校验; v1 在放置/取料区判定前检查物料类型.
        """
        return False

    def _update_conveyor(self) -> None:
        ctx = self._pick_context()
        rec = self.materials.by_uid(self.uid)
        if rec is None:
            self._fail("UID_NOT_REGISTERED")
            return
        if self.state not in (UnitTaskState.IDLE,
                              UnitTaskState.WAITING_FOR_ITEM):
            return
        if rec.state not in (MaterialState.SPAWNED,
                             MaterialState.ON_CONVEYOR):
            return
        if self.fault in (base.FAULT_NO_TARGET, base.FAULT_UID_MISMATCH):
            self.conveyor.set_speed(ctx.conveyor_alias, 0.0)
            return

        self.conveyor.set_speed(ctx.conveyor_alias, ctx.belt_speed)
        if rec.sim_handle is None:
            self._fail("MATERIAL_HAS_NO_HANDLE")
            return
        try:
            pose = list(self.sim.getObjectPose(rec.sim_handle,
                                               self.sim.handle_world))
        except Exception as exc:
            self._fail(f"MATERIAL_POSE_UNREADABLE: {exc}")
            return
        try:
            compensation_allowed(pose[:3], base.CONVEYOR_SURFACE_Z,
                                 geometry=ctx.belt_geometry)
        except TransportFault as exc:
            rec.transition_to(MaterialState.FAULT, self.sim_time,
                              f"CONVEYOR_FAULT: {exc}")
            self._fail(f"CONVEYOR_FAULT: {exc}")
            return

        previous = self._material_prev_pos or pose
        step = bounded_transport_step(
            pose[:3], previous, ctx.belt_geometry, ctx.belt_speed,
            base.SIM_STEP)
        if step.delta > 0.0:
            self.sim.setObjectPosition(rec.sim_handle, step.new_position,
                                       self.sim.handle_world)
            self.compensation_count += 1
            self._compensation_steps += 1
            self._compensation_consecutive += 1
            if self._compensation_consecutive > \
                    cfg.COMPENSATION_MAX_CONSECUTIVE:
                self._long_compensation = True
            self._event(
                f"HYBRID_DYNAMIC compensation Δ={step.delta:.4f} m "
                f"(physical={step.physical_delta:.4f}, "
                f"shortfall={step.shortfall:.4f})")
            self._material_prev_pos = list(step.new_position)
        else:
            self._compensation_consecutive = 0
            self._material_prev_pos = list(pose[:3])
        if step.overspeed:
            self._overspeed_steps += 1
            if self._overspeed_steps <= 3:
                self._event(
                    f"TRANSPORT_OVERSPEED: physical={step.physical_delta:.4f} "
                    f"> expected={ctx.belt_speed * base.SIM_STEP:.4f}")

        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              f"entered {ctx.label}conveyor effective zone")
        if step.at_stop:
            self._fail("NO_SENSOR_TRIGGER_AT_STOP: "
                       f"{ctx.label}物料到达停止区但传感器未命中目标")

    def _read_pick_sensor(self) -> None:
        if self.state is not UnitTaskState.WAITING_FOR_ITEM:
            return
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            return
        expected = int(rec.sim_handle)
        hits = self._pick_sensor_hits()
        if expected in hits:
            # 步骤 1.1 B 组: 传感器命中目标 UID 即下发零速, 不等行程进度。
            self.last_mismatch = None
            self._mismatch_logged = False
            self._mismatch_since = None
            self._command_pick_stop("sensor_hit")
            return
        if hits and expected not in hits:
            wrong = int(hits[0])
            self.last_mismatch = PickMismatch(
                self.sim_time, MismatchReason.UID_MISMATCH, wrong, expected)
            if self._mismatch_since is None:
                self._mismatch_since = self.sim_time
            if not self._mismatch_logged and self.logger:
                self.logger.warning(
                    "[MISMATCH] PICK_MISMATCH: %s传感器命中 %s, "
                    "当前任务 uid=%s handle=%s; 不吸附",
                    self._pick_context().label, wrong, self.uid, expected)
                self._mismatch_logged = True
            return
        if self.last_mismatch is None or \
                self.last_mismatch.reason is MismatchReason.UID_MISMATCH:
            self.last_mismatch = PickMismatch(
                self.sim_time, MismatchReason.NO_HIT, -1, expected)
        self._mismatch_since = None

    def _command_pick_stop(self, reason: str) -> None:
        if self._stop_command_time is not None:
            return
        ctx = self._pick_context()
        rec = self.materials.by_uid(self.uid)
        position = None
        if rec is not None and rec.sim_handle is not None:
            try:
                position = list(self.sim.getObjectPosition(
                    rec.sim_handle, self.sim.handle_world))
            except Exception:
                position = None
        if position is None:
            self._safety_fail("STOP_POSE_UNREADABLE: 停带命令前无法读取物料位姿")
            return
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(ctx.conveyor_alias),
            base.CONVEYOR_STATE_KEY)
        self.conveyor.stop(ctx.conveyor_alias)
        self._stop_command_time = self.sim_time
        self._stop_monitor = ConveyorStopMonitor(ctx.belt_geometry)
        self._stop_monitor.on_stop_command(
            self.sim_time, position,
            speed.value if speed.valid else ctx.belt_speed)
        self._pad_pos_at_stop = self._pad_position()
        self._pad_prev_pos = self._pad_pos_at_stop
        self._enter(UnitTaskState.STOPPING,
                    f"{ctx.label}conveyor stop commanded ({reason})")

    def _advance_stopping(self) -> None:
        if self._stop_command_time is None:
            self._command_pick_stop("unspecified")
            return
        if self._stop_monitor is None:
            self._safety_fail("STOP_MONITOR_MISSING")
            return
        if self.sim_time - self._stop_command_time > \
                cfg.CONVEYOR_STOP_TIMEOUT:
            self._fail("PAD_STOP_TIMEOUT: 带面未在限时内停止")
            return
        ctx = self._pick_context()
        rec = self.materials.by_uid(self.uid)
        position = None
        if rec is not None and rec.sim_handle is not None:
            try:
                position = list(self.sim.getObjectPosition(
                    rec.sim_handle, self.sim.handle_world))
            except Exception:
                position = None
        if position is None:
            self._safety_fail("STOP_POSITION_UNKNOWN: 停带观测缺失物料位姿")
            return
        speed = read_conveyor_speed(
            self.sim, self.scene.handle(ctx.conveyor_alias),
            base.CONVEYOR_STATE_KEY)
        status = self._stop_monitor.observe(
            self.sim_time, speed, position,
            pad_position=self._pad_position())
        if status is StopStatus.PENDING:
            return
        measurement = self._stop_monitor.measurement
        if status is StopStatus.OK:
            self._event(f"CONVEYOR_STOP OK: {measurement.as_dict()}")
            self._stability = MaterialStabilityTracker(
                self.sim_time, position)
            self._enter(UnitTaskState.MATERIAL_SETTLING,
                        "conveyor stopped, waiting material stable")
            return
        if status is StopStatus.STOP_UNCONFIRMED:
            self._safety_fail(
                f"STOP_UNCONFIRMED: {measurement.message}",
                measurement.as_dict())
            return
        if rec is not None:
            rec.transition_to(MaterialState.FAULT, self.sim_time,
                              f"CONVEYOR_STOP_{status.value}")
        self._fail(f"CONVEYOR_STOP_{status.value}: {measurement.message}")

    def _advance_material_settling(self) -> None:
        rec = self.materials.by_uid(self.uid)
        if rec is None or rec.sim_handle is None:
            self._fail("UID_NOT_REGISTERED")
            return
        speed = self._read_material_speed(rec.sim_handle)
        position = self._read_material_position(rec.sim_handle)
        if speed is None or position is None:
            self._safety_fail("MATERIAL_STATE_UNREADABLE: "
                              "无法读取物料速度/位姿")
            return
        if self._stability is None:
            self._stability = MaterialStabilityTracker(
                self.sim_time, position)
        state = self._stability.update(self.sim_time, position, speed)
        if state.timed_out:
            self._fail(
                "MATERIAL_SETTLE_TIMEOUT: 物料未在限时内稳定 "
                f"(speed={speed:.3f} m/s, step={state.step_moved:.4f} m)")
            return
        if not state.stable:
            return

        try:
            pose = [float(v) for v in self.sim.getObjectPose(
                rec.sim_handle, self.sim.handle_world)]
            box = self.suction.material_box(rec.sim_handle)
        except Exception as exc:
            self._fail(f"MATERIAL_GEOMETRY_UNREADABLE: {exc}")
            return
        tilt = quat_tilt(pose[3:7])
        if self._check_pick_candidate(rec, pose):
            return
        if not self._pick_context().pick_region.contains(pose):
            self._fail(
                "OUT_OF_ALLOWED_REGION: 物料停稳后不在允许抓取区 "
                f"(x={pose[0]:.4f}, y={pose[1]:.4f}, z={pose[2]:.4f})")
            return
        if tilt > base.MATERIAL_MAX_TILT:
            self._fail(f"MATERIAL_TILT: 物料倾斜 {tilt:.3f} rad")
            return

        self._pick_pose = pose
        self._pick_box = box
        self._pick_tcp = contact_pose(pose, box, 0.0)
        if rec.state is MaterialState.SPAWNED:
            rec.transition_to(MaterialState.ON_CONVEYOR, self.sim_time,
                              "settled on conveyor")
        if rec.state is MaterialState.ON_CONVEYOR:
            rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                              "stopped and stable, measured pose")
        self._event(
            f"material stable at ({pose[0]:.4f},{pose[1]:.4f},"
            f"{pose[2]:.4f}) tilt={tilt:.3f}; surface contact target fixed")
        self._begin_motion(UnitTaskState.APPROACHING,
                           "measured pose after active stop")

    # ═══════════════════════════════════════════════════════
    # 读数 / 失败
    # ═══════════════════════════════════════════════════════

    def _read_material_speed(self, handle: int) -> Optional[float]:
        try:
            value = self.sim.getObjectVelocity(handle)
        except Exception:
            return None
        lin = value[0] if isinstance(value, (tuple, list)) and len(value) == 2 \
            else list(value)[:3]
        try:
            return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))
        except Exception:
            return None

    def _read_material_position(self, handle: int) -> Optional[list[float]]:
        try:
            return [float(v) for v in self.sim.getObjectPosition(
                handle, self.sim.handle_world)]
        except Exception:
            return None

    def _ik_failed(self, message: str) -> None:
        # 阶段二不在失败后自动重试; 重新规划必须由显式新任务触发.
        self._fail(f"IK_FAILED: {message}")

    def _maybe_retry(self) -> None:
        return

    def _capture_motion_report(self, reason: str) -> None:
        """失败/锁存时保留最后一段计划的实测统计, 不丢证据."""
        if self._motion_reported:
            return
        if self._executor is None and self._plan is None:
            return
        report = {
            "state": self.state.name,
            "status": "FAILED",
            "reason": reason,
            "branch_anchor": self._branch_anchor,
            "plan": (self._last_plan.summary_dict()
                     if self._last_plan is not None else None),
        }
        if self._executor is not None:
            try:
                report["execution"] = self._executor.execution_metrics()
            except Exception as exc:
                report["execution_error"] = f"{type(exc).__name__}: {exc}"
        self._motion_reports.append(report)
        self._motion_reported = True

    def _fail(self, reason: str) -> None:
        self._capture_motion_report(reason)
        if self._executor is not None:
            self._executor.cancel()
            self._executor = None
        self._plan = None
        super()._fail(reason)

    # ═══════════════════════════════════════════════════════
    # 摘要
    # ═══════════════════════════════════════════════════════

    def summary(self) -> dict:
        data = super().summary()
        data.update({
            "controller_id": cfg.STAGE2_CONTROLLER_ID,
            "stage2": True,
            "motion_opt_version": cfg.MOTION_OPT_VERSION,
            "transfer_trajectory_version": cfg.TRANSFER_TRAJECTORY_VERSION,
            "transfer_height": cfg.TRANSFER_HEIGHT,
            "transfer_check": self._transfer_check,
            "safety": self.safety.latch.as_dict(),
            "plan": self._last_plan.as_dict() if self._last_plan else None,
            "motion": self._motion_reports,
            "motion_totals": _motion_totals(self._motion_reports),
            "branch_anchor": self._branch_anchor,
            "stop": (self._stop_monitor.measurement.as_dict()
                     if self._stop_monitor else None),
            "stability": (self._stability.state.as_dict()
                          if self._stability else None),
            "compensation": {
                "count": self._compensation_steps,
                "long_compensation": self._long_compensation,
                "overspeed_steps": self._overspeed_steps,
            },
            "contact": (self.suction.last_contact.as_dict()
                        if getattr(self.suction, "last_contact", None)
                        else None),
            "fk": self._last_fk_report,
            "attach_transform": (
                None if getattr(self.suction, "attach_transform", None) is None
                else [round(float(v), 6) for v in
                      self.suction.attach_transform]),
            "release": {
                "pose": (None if self._release_pose is None
                         else [round(float(v), 6)
                               for v in self._release_pose]),
            },
            "pick_box": (None if self._pick_box is None else {
                "min": list(self._pick_box.minimum),
                "max": list(self._pick_box.maximum)}),
        })
        return data


def _motion_params() -> list[float]:
    return [cfg.EXEC_JOINT_VEL, cfg.EXEC_JOINT_ACCEL, cfg.EXEC_JOINT_JERK]


def lock_for(name: str):
    """阶段 2.01 段内固定 q[0] 策略: 值在规划时取段起始构型."""
    return cfg.Q0_LOCK_JOINT if name in cfg.Q0_LOCK_SEGMENTS else None


def transfer_height(height=None) -> float:
    """LIFTING 与 pre_place 的共享高度: 唯一取值入口, 防止两处漂移."""
    return float(cfg.TRANSFER_HEIGHT if height is None else height)


def transfer_height_pose(pose, height=None) -> list[float]:
    """把位姿平移到共享转运高度 H_transfer, 保留 XY 与工具姿态."""
    out = [float(v) for v in pose]
    out[2] = transfer_height(height)
    return out


def lift_pose(pick_tcp, orientation, height=None) -> list[float]:
    """吸附后的抬升目标: 取料 TCP 的 XY、绝对高度 H_transfer、放置姿态.

    工具姿态取放置点姿态, 保证 ``lift`` 与 ``pre_place`` 四元数一致;
    抓取面若有微小倾斜, 其姿态调整发生在 LIFTING 段内, 水平转运段
    不再改变姿态.
    """
    return [float(pick_tcp[0]), float(pick_tcp[1]),
            transfer_height(height),
            *[float(v) for v in orientation[:4]]]


def pre_place_pose(place_pose, height=None) -> list[float]:
    """放置点正上方的共享高度 pre_place: 同 XY、同高度、同工具姿态."""
    return transfer_height_pose(place_pose, height)


def transfer_goals(place_pose, height=None) -> list[Goal]:
    """TRANSFERRING 的直线目标: 固定高度、固定姿态的 ``pre_place``."""
    pre = pre_place_pose(place_pose, height)
    return [Goal("pre_place", pre, cartesian=True, strict_orientation=True)]


def transfer_detour_pose(place_pose, start_pose, offset,
                         height=None) -> list[float]:
    """固定高度横向绕行点: 起止点中点沿水平运动法向偏移 ``offset``."""
    pre = pre_place_pose(place_pose, height)
    start = [float(v) for v in start_pose]
    dx = pre[0] - start[0]
    dy = pre[1] - start[1]
    norm = math.hypot(dx, dy)
    if norm < 1e-9:
        dx, dy, norm = 1.0, 0.0, 1.0
    perp_x, perp_y = -dy / norm, dx / norm
    return [(start[0] + pre[0]) / 2.0 + perp_x * float(offset),
            (start[1] + pre[1]) / 2.0 + perp_y * float(offset),
            transfer_height(height),
            *[float(v) for v in place_pose[3:7]]]


def transfer_goal_variants(place_pose, start_pose, height=None,
                           offsets=None
                           ) -> list[tuple[str, list[Goal], dict]]:
    """TRANSFERRING 候选: 直线优先, 失败后只允许固定高度横向绕行.

    所有候选都只含位置线性、姿态恒定的笛卡尔段, 调用方必须以
    ``direct_only=True`` 规划, 不产生关节中转路径. 绕行点仍在
    ``TRANSFER_HEIGHT``, 通过同一碰撞/分支/净空与水平姿态复核.
    """
    pre = pre_place_pose(place_pose, height)
    variants: list[tuple[str, list[Goal], dict]] = [(
        "horizontal_direct",
        [Goal("pre_place", pre, cartesian=True, strict_orientation=True)],
        {"detour_offset": None, "semantic_points": ["pre_place"]},
    )]
    values = (cfg.TRANSFER_HORIZONTAL_DETOUR_OFFSETS if offsets is None
              else offsets)
    for offset in values:
        if abs(float(offset)) < 1e-9:
            continue
        via = transfer_detour_pose(place_pose, start_pose, offset, height)
        variants.append((
            f"horizontal_detour_{float(offset):+.2f}",
            [Goal("detour", via, cartesian=True, strict_orientation=True),
             Goal("pre_place", pre, cartesian=True, strict_orientation=True)],
            {"detour_offset": round(float(offset), 6),
             "semantic_points": ["detour", "pre_place"]},
        ))
    return variants


def horizontal_transfer_check(waypoints, checker, height=None,
                              orientation=None) -> dict:
    """复核水平段计划: 所有 waypoint 的 TCP 高度与工具姿态偏差.

    返回 ``ok``/``max_z``/``max_ang``/``points``; TCP 不可读时标记
    ``query_unknown=True``, 由调用方按未知查询契约处理.
    """
    target_height = transfer_height(height)
    reference = ([float(v) for v in orientation[:4]]
                 if orientation is not None else None)
    max_z = 0.0
    max_ang = 0.0
    points = 0
    for q in waypoints:
        tcp = checker.tcp_at(q)
        if tcp is None:
            return {"ok": False, "query_unknown": True, "points": points,
                    "max_z": round(max_z, 6), "max_ang": round(max_ang, 6),
                    "height": target_height, "error": "TCP 不可读"}
        if reference is None:
            reference = list(tcp[3:7])
        max_z = max(max_z, abs(float(tcp[2]) - target_height))
        max_ang = max(max_ang, quat_angle_between(tcp[3:7], reference))
        points += 1
    return {
        "ok": (points > 0
               and max_z <= cfg.EXEC_TCP_POS_TOL
               and max_ang <= cfg.EXEC_TCP_ANG_TOL),
        "query_unknown": False,
        "points": points,
        "max_z": round(max_z, 6),
        "max_ang": round(max_ang, 6),
        "height": target_height,
        "pos_tol": cfg.EXEC_TCP_POS_TOL,
        "ang_tol": cfg.EXEC_TCP_ANG_TOL,
    }


def _motion_totals(reports) -> dict:
    done = [report.get("execution") for report in reports
            if report.get("status") == "DONE" and report.get("execution")]
    totals = [entry.get("totals", {}) for entry in done]
    return {
        "motions": len(reports),
        "executed": len(done),
        "failed": sum(1 for report in reports
                      if report.get("status") == "FAILED"),
        "tv_total": round(sum(float(t.get("tv_total") or 0.0)
                              for t in totals), 6),
        "max_step": round(max([float(t.get("max_step") or 0.0)
                               for t in totals] or [0.0]), 6),
        "sim_duration": round(sum(float(t.get("sim_duration") or 0.0)
                                  for t in totals), 4),
        "steps": sum(int(t.get("steps") or 0) for t in totals),
        "commands": sum(int(t.get("commands") or 0) for t in totals),
    }


def _compose(tcp_pose, transform) -> list[float]:
    """TCP 世界位姿 * T_tcp_material → 物料期望世界位姿."""
    p = [float(v) for v in tcp_pose]
    t = [float(v) for v in transform]
    rel = quat_rotate(p[3:7], t[:3])
    return [p[0] + rel[0], p[1] + rel[1], p[2] + rel[2],
            *quat_multiply(p[3:7], t[3:7])]
