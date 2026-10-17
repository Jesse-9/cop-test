"""stage1_unit_controller.py — 阶段一单工位闭环控制循环 (§2.11)

把阶段一的模块组装成一条闭环:
    生成物料 → 传送带输送 → 取料传感器确认
    → UR5 IK 接近 → 指定物料吸附 → 抬升
    → 放到工位或 AGV 载台 → 释放
    → 物料恢复动态属性 → 结果验证

单步顺序 (§2.11, 强制):
  1. 读取通信和仿真状态
  2. 更新 conveyor 驱动和 HYBRID_DYNAMIC 物料输送
  3. 读取 Sensor_PickStation
  4. 若目标到位, 生成或推进 UR5 任务
  5. 求解 IK 并下发关节目标
  6. 检查 TCP 和 Sensor_SuctionPad, 执行吸附
  7. 执行抬升、转运和放置
  8. 释放物料并等待稳定
  9. 若目标是 AGV, 更新 pioneer p3dx 轮速
  10. 写入事件日志
  11. 调用 sim.step()

不变量:
  - 传感器命中 ≠ 自动吸附: 必须校验当前任务 UID (§2.8)
  - IK 失败 → FAILED + 退避重试, 绝不写回伪造角度 (§2.7 步骤9)
  - sim.step() 永远是本步最后动作
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

import factory_config as cfg
from material_manager import MaterialState


# ═══════════════════════════════════════════════════════════════
# 任务状态与误配记录
# ═══════════════════════════════════════════════════════════════

class UnitTaskState(Enum):
    IDLE              = auto()   # 未开始
    WAITING_FOR_ITEM  = auto()   # 等待目标物料到达取料点
    APPROACHING       = auto()   # IK 接近取料位
    ATTACHING         = auto()   # 吸附
    LIFTING           = auto()   # 抬升
    TRANSFERRING      = auto()   # 转运到放置点上方
    PLACING           = auto()   # 下探放置
    RELEASING         = auto()   # 释放并等待稳定
    DONE              = auto()   # 完成
    FAILED            = auto()   # 失败 (退避后可重试)


class MismatchReason(Enum):
    NO_HIT       = auto()   # 传感器无命中
    UID_MISMATCH = auto()   # 命中另一物料 (PICK_MISMATCH)


@dataclass
class PickMismatch:
    sim_time: float
    reason: MismatchReason
    hit_handle: int
    expected_handle: int


# ═══════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════

class Stage1UnitController:
    """阶段一单元闭环控制器 (单目标物料)。

    Args:
        sim:          Remote API sim 模块。
        handles:      句柄访问器 (read_pick_sensor() → 命中的物料句柄或 -1)。
        materials:    MaterialManager。
        conveyor:     ConveyorController。
        ik:           Ur5IkController。
        suction:      SuctionController。
        agv:          PioneerController (可选, 目标为 AGV 载台时使用)。
        target_uid:   本任务的物料 UID (§2.5 步骤7: 引用 UID, 不用 alias)。
        conveyor_alias: 取料传送带别名。
        pick_pose:    取料位 TCP 目标位姿 (7 元素)。
        place_pose:   放置位 TCP 目标位姿 (7 元素)。
        lift_pose:    抬升中间位姿 (可选, 缺省为 pick_pose 上方)。
        logger:       可选日志。
    """

    def __init__(self, sim, handles, materials, conveyor, ik, suction,
                 agv=None, target_uid: int = -1,
                 conveyor_alias: str = "", pick_pose: Optional[list] = None,
                 place_pose: Optional[list] = None,
                 lift_pose: Optional[list] = None,
                 logger: Optional[logging.Logger] = None):
        self.sim = sim
        self.handles = handles
        self.materials = materials
        self.conveyor = conveyor
        self.ik = ik
        self.suction = suction
        self.agv = agv
        self.target_uid = target_uid
        self.conveyor_alias = conveyor_alias
        self.pick_pose = list(pick_pose or [0, 0, 0, 0, 0, 0, 1])
        self.place_pose = list(place_pose or [0, 0, 0, 0, 0, 0, 1])
        self.lift_pose = list(lift_pose or [
            self.pick_pose[0], self.pick_pose[1],
            self.pick_pose[2] + cfg.ARM_PICK_APPROACH_Z,
            self.pick_pose[3], self.pick_pose[4], self.pick_pose[5],
            self.pick_pose[6],
        ])
        self.logger = logger

        self.task_state = UnitTaskState.IDLE
        self.last_mismatch: Optional[PickMismatch] = None
        self.retry_at: Optional[float] = None
        self.sim_time = 0.0
        self._mismatch_logged = False

    # ── 单步 ────────────────────────────────────────────────────

    def step(self) -> None:
        """执行一个 §2.11 控制步 (顺序固定, sim.step() 最后)"""
        sim = self.sim
        self.sim_time = self._read_sim_time()

        # 1. 读取通信和仿真状态
        state = self._read_sim_state()

        # 2. conveyor 驱动 (物料在带面上等待时保持运行)
        self._update_conveyor()

        # 3. 读取 Sensor_PickStation + 4. 推进任务状态机
        self._advance_task()

        # 5-8. IK / 吸附 / 抬升转运放置 / 释放
        self._run_motion()

        # 9. AGV 轮速 (目标为 AGV 载台时)
        self._update_agv()

        # 10. 事件日志
        self._log_events()

        # 11. 推进物理引擎 (永远最后)
        sim.step()

    # ── 各步骤实现 ──────────────────────────────────────────────

    def _read_sim_time(self) -> float:
        try:
            return float(self.sim.getSimulationTime())
        except Exception:
            return self.sim_time

    def _read_sim_state(self) -> int:
        try:
            return int(self.sim.getSimulationState())
        except Exception:
            return -1

    def _update_conveyor(self) -> None:
        """§2.11 步骤2: 目标尚未被取走时保持带速"""
        if self.conveyor_alias and self.task_state in (
                UnitTaskState.IDLE, UnitTaskState.WAITING_FOR_ITEM):
            try:
                self.conveyor.set_speed(self.conveyor_alias, cfg.CONVEYOR_SPEED)
            except Exception:
                pass

    def _advance_task(self) -> None:
        """§2.11 步骤3-4: 读传感器并推进任务状态机"""
        rec = self.materials.by_uid(self.target_uid)
        if rec is None:
            self.task_state = UnitTaskState.FAILED
            return

        # 完成/失败后除重试外不再动作
        if self.task_state is UnitTaskState.DONE:
            return
        if self.task_state is UnitTaskState.FAILED:
            self._maybe_retry()
            return

        if self.task_state is UnitTaskState.IDLE:
            self.task_state = UnitTaskState.WAITING_FOR_ITEM

        if self.task_state is UnitTaskState.WAITING_FOR_ITEM:
            hit = self._read_pick_sensor()
            expected = rec.sim_handle
            if hit is None or hit < 0:
                self.last_mismatch = PickMismatch(
                    self.sim_time, MismatchReason.NO_HIT, -1, expected or -1)
                return
            if expected is not None and int(hit) != int(expected):
                # §2.8 步骤5: 命中非目标物料 → PICK_MISMATCH, 不吸附
                self.last_mismatch = PickMismatch(
                    self.sim_time, MismatchReason.UID_MISMATCH,
                    int(hit), int(expected))
                if not self._mismatch_logged and self.logger:
                    self.logger.warning(
                        f"[MISMATCH] PICK_MISMATCH: 传感器命中 {hit}, "
                        f"目标 uid={self.target_uid} (handle={expected})")
                    self._mismatch_logged = True
                return
            # 命中目标 → 物料进入可吸附状态 (§2.5)
            if rec.state is MaterialState.ON_CONVEYOR:
                rec.transition_to(MaterialState.READY_FOR_PICK, self.sim_time,
                                  reason="Sensor_PickStation confirmed UID")
            self.last_mismatch = None
            self.task_state = UnitTaskState.APPROACHING

    def _maybe_retry(self) -> None:
        """§2.12: FAILED 后经过退避时间回到等待, 重新尝试"""
        if self.retry_at is None:
            return
        if self.sim_time >= self.retry_at:
            rec = self.materials.by_uid(self.target_uid)
            if rec is not None and rec.state is MaterialState.READY_FOR_PICK:
                self.task_state = UnitTaskState.WAITING_FOR_ITEM
            elif rec is not None and rec.state is MaterialState.ON_CONVEYOR:
                self.task_state = UnitTaskState.WAITING_FOR_ITEM
            self.retry_at = None

    def _run_motion(self) -> None:
        """§2.11 步骤5-8: IK → 吸附 → 抬升/转运/放置 → 释放"""
        st = self.task_state
        if st in (UnitTaskState.IDLE, UnitTaskState.WAITING_FOR_ITEM,
                  UnitTaskState.DONE, UnitTaskState.FAILED):
            return

        if st is UnitTaskState.APPROACHING:
            if self._solve_to(self.pick_pose):
                self.task_state = UnitTaskState.ATTACHING
            return

        if st is UnitTaskState.ATTACHING:
            from suction_controller import ATTACH_OK
            result = self.suction.attach(self.target_uid, self.sim_time)
            if result.code == ATTACH_OK:
                self.task_state = UnitTaskState.LIFTING
            else:
                self._fail(f"吸附被拒: {result.code} ({result.message})")
            return

        if st is UnitTaskState.LIFTING:
            if self._solve_to(self.lift_pose):
                self.task_state = UnitTaskState.TRANSFERRING
            return

        if st is UnitTaskState.TRANSFERRING:
            if self._solve_to(self.place_pose):
                self.task_state = UnitTaskState.PLACING
            return

        if st is UnitTaskState.PLACING:
            self.task_state = UnitTaskState.RELEASING
            return

        if st is UnitTaskState.RELEASING:
            rec = self.materials.by_uid(self.target_uid)
            if rec is not None and rec.state is MaterialState.HELD_BY_SUCTION:
                self.suction.release(self.target_uid, self.sim_time,
                                     self.place_pose)
            self.task_state = UnitTaskState.DONE
            if self.logger:
                self.logger.info(
                    f"[UNIT] uid={self.target_uid} 闭环完成 (sim_time={self.sim_time:.2f}s)")
            return

    def _solve_to(self, pose: list) -> bool:
        """求解一次 IK; 失败进入 FAILED 并安排退避重试 (§2.7 步骤8-9)"""
        result = self.ik.solve_target(pose)
        if result.success:
            return True
        self._fail(result.message)
        return False

    def _fail(self, message: str) -> None:
        self.task_state = UnitTaskState.FAILED
        self.retry_at = self.sim_time + cfg.CONVEYOR_PICKUP_RETRY_DELAY
        if self.logger:
            self.logger.warning(
                f"[UNIT] uid={self.target_uid} 任务失败: {message}; "
                f"退避至 {self.retry_at:.2f}s 后重试")

    def _update_agv(self) -> None:
        """§2.11 步骤9: 目标为 AGV 载台时, 装卸期间车体保持静止"""
        if self.agv is None:
            return
        if self.task_state in (UnitTaskState.PLACING,
                               UnitTaskState.RELEASING):
            self.agv.stop()

    def _log_events(self) -> None:
        """§2.11 步骤10: 事件日志 (DEBUG 级逐步, WARNING 级已在上文)"""
        if self.logger:
            self.logger.debug(
                f"[UNIT] t={self.sim_time:.2f}s state={self.task_state.name} "
                f"uid={self.target_uid}")

    def _read_pick_sensor(self) -> int:
        """§2.11 步骤3: 读取取料传感器命中的物料句柄 (-1 = 无命中)"""
        return int(self.handles.read_pick_sensor())
