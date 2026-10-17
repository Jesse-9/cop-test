"""ur5_ik_controller.py — UR5 simIK 逆运动学控制器 (阶段一 §2.7 / CLAUDE §5)

为单台 UR5 建立独立的 simIK 环境，包含主求解组（伪逆）与备用组（阻尼
最小二乘)。目标姿态通过 `SuctionPad_*_TCP` dummy 作为 IK tip；求解
约束使用 `constraint_position`（姿态约束在抓取时刻由 ARM_TOOL_MAX_TILT
拦截，见 factory_controller）。

失败时尝试备用组；两组均失败返回明确状态，不写回伪造角度 (CLAUDE §5)。

方案依据:
  docs/wiki/06-工业案例/06-05-柔性工业车间分阶段执行步骤.md §2.7
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import factory_config as cfg


# ═══════════════════════════════════════════════════════════════
# 异常与结果码
# ═══════════════════════════════════════════════════════════════

class IKError(Exception):
    """simIK 求解失败 (主组+备用组均失败，不可达)"""


@dataclass
class IKResult:
    success: bool
    joints: list[float]   # 6 个关节目标角度 (rad)，success=False 时为 []
    result_code: int      # simIK.handleGroup 原始 result
    reason: int           # simIK.handleGroup 原始 reason
    error: list[float]    # [linear_error, angular_error]
    used_backup: bool     # 是否由备用组求解
    message: str          # 人类可读原因


# ═══════════════════════════════════════════════════════════════
# UR5 DH 参数 (供模拟/纯逻辑测试用，不与 simIK API 混用)
# ═══════════════════════════════════════════════════════════════

# UR5 (Modified DH): [a, d, alpha] 与 关节偏移
_UR5_DH = [
    (0.0,    0.089159,  math.pi / 2),   # j1 底座
    (-0.425, 0.0,      0.0),            # j2 肩
    (-0.39225, 0.0,     0.0),           # j3 肘
    (0.0,    0.10915,  math.pi / 2),    # j4 腕1
    (0.0,    0.09465, -math.pi / 2),    # j5 腕2
    (0.0,    0.0823,   0.0),             # j6 腕3
]
UR5_REACH_RADIUS = 0.85  # UR5 有效工作半径 (m)


# ═══════════════════════════════════════════════════════════════
# 控制器
# ═══════════════════════════════════════════════════════════════

class Ur5IkController:
    """单台 UR5 simIK 控制器。

    Args:
        sim:      Remote API sim 模块。
        root_handle: UR5 模型根句柄。
        base_handle: UR5 基座句柄。
        tcp_handle:  末端 TCP (SuctionPad_*_TCP dummy / connection) 句柄。
        target_handle: IK 目标 dummy 句柄。
        joint_handles: 六个本体关节句柄 (base→tip).
        max_vel:     关节最大角速度 (rad/s)。
        max_accel:   关节最大角加速度 (rad/s²)。
    """

    def __init__(self, sim, root_handle: int, base_handle: int,
                 tcp_handle: int, target_handle: int,
                 joint_handles: list[int],
                 max_vel: float = 1.0,
                 max_accel: float = 5.0):
        self.sim = sim
        self.root_handle = root_handle
        self.base_handle = base_handle
        self.tcp_handle = tcp_handle
        self.target_handle = target_handle
        self.joint_handles = list(joint_handles)
        self.max_vel = max_vel
        self.max_accel = max_accel

        self.env = -1
        self.group_main = -1
        self.group_backup = -1
        self.initialized = False

        # 对 IK target 的每步位移上限，避免一次性跳出可达域 (§2.7 实测: ≥0.7 m 双组均失败)
        self.max_target_step = 0.02  # m

        # 累积的目标位姿（由 solve_target 逐步逼近）
        self._current_target_pose: Optional[list[float]] = None

    # ── 初始化 ──────────────────────────────────────────────────

    def initialize(self) -> None:
        """建立独立 IK environment + 主/备 group。

        §2.7 步骤1-4: simIK environment per UR5; 主组伪逆；备用组阻尼最小二乘；
        用 UR5 base、TCP、target 建 IK element。
        """
        sim = self.sim
        if self.initialized:
            return

        self.env = sim.simIKCreateEnvironment()
        # 主组: 伪逆，最大迭代次数由 simIK 示例值 (可调)
        self.group_main = sim.simIKCreateGroup(self.env)
        sim.simIKSetGroupCalculation(self.group_main, sim.simIK_pseudoinverse, 100)
        # 备用组: 阻尼最小二乘
        self.group_backup = sim.simIKCreateGroup(self.env)
        sim.simIKSetGroupCalculation(self.group_backup, sim.simIK_dampedLeastSquares, 100)

        # 加入 UR5 本体关节
        for jh in self.joint_handles:
            sim.simIKAddElementFromScene(self.env, self.group_main,
                                         self.base_handle, jh,
                                         sim.simIKconstraint_position)
            sim.simIKAddElementFromScene(self.env, self.group_backup,
                                         self.base_handle, jh,
                                         sim.simIKconstraint_position)

        # IK tip 与 target 元素
        sim.simIKAddElementFromScene(self.env, self.group_main,
                                     self.base_handle, self.tcp_handle,
                                     sim.simIKconstraint_position)
        sim.simIKAddElementFromScene(self.env, self.group_main,
                                     self.base_handle, self.target_handle,
                                     sim.simIKconstraint_position)
        sim.simIKAddElementFromScene(self.env, self.group_backup,
                                     self.base_handle, self.tcp_handle,
                                     sim.simIKconstraint_position)
        sim.simIKAddElementFromScene(self.env, self.group_backup,
                                     self.base_handle, self.target_handle,
                                     sim.simIKconstraint_position)

        self.initialized = True

    # ── 同步 (simIK 世界 ↔ 场景世界) ──────────────────────────────

    def sync_from_sim(self) -> None:
        """把当前场景关节/位姿同步到 IK 世界 (§2.7 步骤5-6 的配套)。
        CoppeliaSim 官方: handleGroup 之前必须 simIKSyncFromSim(env, [group])。
        """
        sim = self.sim
        sim.simIKSyncFromSim(self.env, [self.group_main, self.group_backup])

    def sync_to_sim(self) -> None:
        """把 IK 世界求解结果写回场景关节 (§2.7 步骤8 的配套)。"""
        sim = self.sim
        sim.simIKSyncToSim(self.env, [self.group_main, self.group_backup])

    # ── 求解 ────────────────────────────────────────────────────

    def solve_target(self, target_pose: list[float]) -> IKResult:
        """求解目标位姿的 6 个关节角。

        §2.7 步骤7: 返回关节角、结果码、原因和误差。
        §2.7 步骤8-9: 主组失败 → 备用组；两组均失败返回 IKError（不写伪造角度)。
        段进限制: target 与当前目标位姿位移超过 max_target_step 时分步逼近。

        Returns:
            IKResult(success=True) 时 joints 填充 6 个角度；
            success=False 时 joints=[] 并 message 说明失败原因。
        """
        sim = self.sim
        if not self.initialized:
            return IKResult(False, [], -1, -1, [0.0, 0.0], False,
                            "IK 未初始化")

        # 段进逼近: 避免一次性目标跳出可达域 (§2.7 实测)
        target_pose = self._step_toward(target_pose)

        # 更新 IK 目标位姿 (target dummy 位置)
        sim.setObjectPosition(self.target_handle,
                              target_pose[:3], sim.handle_world)
        if len(target_pose) >= 7:
            sim.setObjectQuaternion(self.target_handle,
                                    target_pose[3:7], sim.handle_world)

        # 同步场景 → IK 世界
        self.sync_from_sim()

        # 主组求解
        result = self._handle_group(self.group_main, "主组(伪逆)")
        if result.success:
            result.used_backup = False
            return result

        # 备用组求解 (§2.7 步骤8)
        result = self._handle_group(self.group_backup, "备用组(阻尼最小二乘)")
        result.used_backup = True
        if result.success:
            return result

        # 两组均失败 → 明确失败，不写回伪造角度 (§2.7 步骤9)
        return IKResult(False, [], result.result_code, result.reason,
                        result.error, True,
                        f"IK 不可达: 主组与备用组均失败 "
                        f"(线误差 {result.error[0]:.4f} m, 角误差 {result.error[1]:.4f} rad)")

    def _handle_group(self, group: int, label: str) -> IKResult:
        sim = self.sim
        res = sim.simIKHandleGroup(group)
        # §5 实测: simIK.handleGroup 返回三元组 (result, reason, [lin, ang])
        if isinstance(res, (tuple, list)) and len(res) >= 3:
            result_code, reason, err = res[0], res[1], list(res[2])
        else:
            result_code, reason, err = int(res), 0, [0.0, 0.0]

        success = (result_code == 1)  # simIK.result_success = 1
        if not success:
            return IKResult(False, [], result_code, reason, err, False,
                            f"{label} 求解失败 (result={result_code}, reason={reason})")

        # 读取 6 个关节的 IK 目标角度并写回场景
        joints = []
        for jh in self.joint_handles:
            try:
                pos = sim.getJointPosition(jh)
                joints.append(float(pos))
            except Exception:
                joints.append(0.0)
        self.sync_to_sim()
        return IKResult(True, joints, result_code, reason, err, False,
                        f"{label} 求解成功")

    # ── 辅助 ────────────────────────────────────────────────────

    def _step_toward(self, target: list[float]) -> list[float]:
        """把目标位姿按 max_target_step 逐步逼近当前目标，返回实际下发位姿。"""
        if self._current_target_pose is None:
            self._current_target_pose = list(target)
            return list(target)
        cur = self._current_target_pose
        dx = target[0] - cur[0]; dy = target[1] - cur[1]; dz = target[2] - cur[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > self.max_target_step:
            s = self.max_target_step / dist
            cur = [cur[0] + dx * s, cur[1] + dy * s, cur[2] + dz * s,
                   cur[3], cur[4], cur[5], cur[6]]
        self._current_target_pose = list(cur)
        return list(cur)

    def reachable(self, target_pose: list[float]) -> bool:
        """快速判断目标是否在工作半径内（仅距离粗筛，不替代真实求解）。"""
        try:
            base_pose = sim_get_position(self.sim, self.base_handle)
        except Exception:
            base_pose = [0.0, 0.0, 0.0]
        d = math.sqrt(sum((target_pose[k] - base_pose[k]) ** 2 for k in range(3)))
        return d <= UR5_REACH_RADIUS + cfg.ARM_REACH_MARGIN if hasattr(cfg, "ARM_REACH_MARGIN") else d <= UR5_REACH_RADIUS + 0.05


def sim_get_position(sim, handle: int) -> list[float]:
    p = sim.getObjectPosition(handle, sim.handle_world)
    return list(p)