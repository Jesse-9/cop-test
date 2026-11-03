"""ur5_ik_controller.py — UR5 simIK 求解与关节目标 (§2.7).

每个控制器持有**独立** simIK environment; 主组伪逆, 备用组阻尼最小二乘。
求解只读取 IK 环境内的关节角, 再由调用方通过 `sim.setJointTargetPosition`
下发 —— 不使用 `syncWorlds=True` / `syncToSim` 直接写场景关节。

4.10 实测:
  - `addElementFromScene(env, group, base, tip, target, constraints)`
    返回 `(result, scene→ik 映射, ik→scene 映射)`。
  - `handleGroup(env, group, {})` 返回 `(result, reason, [lin, ang])`。
  - 目标 dummy 先 `setObjectPose`, 再 `syncFromSim` 才会进入 IK 世界。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config_base as cfg


class IKError(Exception):
    """IK 求解异常 (配置/环境错误)"""


@dataclass
class IKResult:
    success: bool
    joints: list[float] = field(default_factory=list)
    result_code: int = -1
    reason: int = -1
    precision: list[float] = field(default_factory=list)
    used_backup: bool = False
    message: str = ""
    used_seed: bool = False


class Ur5IkController:
    def __init__(self, sim, simIK, arm_root: int, tcp_handle: int,
                 target_handle: int, scene_joints: list[int],
                 constraint: int | None = None) -> None:
        self.sim = sim
        self.simIK = simIK
        self.arm_root = int(arm_root)
        self.tcp_handle = int(tcp_handle)
        self.target_handle = int(target_handle)
        self.scene_joints = [int(j) for j in scene_joints]
        # 生产控制使用 constraint_pose; 步骤 1.1 的诊断扫描另建
        # constraint_position 实例, 用于区分“位置不可达”和“姿态不可达”。
        self.constraint = (simIK.constraint_pose if constraint is None
                           else int(constraint))

        self.env = -1
        self.group_main = -1
        self.group_backup = -1
        self._maps: dict[int, dict[int, int]] = {}
        self._elements: dict[int, int] = {}
        self.initialized = False

    # ── 初始化 ──────────────────────────────────────────────

    def initialize(self) -> None:
        if self.initialized:
            return
        sim, ik = self.sim, self.simIK
        self.env = ik.createEnvironment()
        self.group_main = ik.createGroup(self.env)
        self.group_backup = ik.createGroup(self.env)

        ik.setGroupCalculation(self.env, self.group_main,
                               ik.method_pseudo_inverse, 0.0,
                               cfg.ARM_IK_MAX_ITERATIONS)
        ik.setGroupCalculation(self.env, self.group_backup,
                               ik.method_damped_least_squares, 1.0,
                               cfg.ARM_IK_MAX_ITERATIONS)

        for group in (self.group_main, self.group_backup):
            ret = ik.addElementFromScene(
                self.env, group, self.arm_root, self.tcp_handle,
                self.target_handle, self.constraint)
            self._maps[group] = self._extract_joint_map(ret, group)
            # 4.10: 第一个返回值是 IK element handle
            element = int(ret[0]) if isinstance(ret, (tuple, list)) else -1
            self._elements[group] = element
            if element >= 0 and hasattr(ik, "setElementPrecision"):
                try:
                    ik.setElementPrecision(self.env, group, element,
                                           list(cfg.ARM_SOLVE_PRECISION))
                except Exception:
                    pass

        self.initialized = True

    def _extract_joint_map(self, ret, group: int) -> dict[int, int]:
        """从 addElementFromScene 返回值提取 {scene_joint: ik_joint}。"""
        mapping: dict[int, int] = {}
        if isinstance(ret, (tuple, list)) and len(ret) >= 2:
            sim_to_ik = ret[1]
            if isinstance(sim_to_ik, dict):
                for scene_j, ik_j in sim_to_ik.items():
                    try:
                        if int(scene_j) in self.scene_joints:
                            mapping[int(scene_j)] = int(ik_j)
                    except (TypeError, ValueError):
                        continue
        if not mapping:
            # 回退: 依赖 getGroupJoints 的链序 (tip→base), 与 scene_joints 反序对应
            try:
                ik_joints = list(self.simIK.getGroupJoints(self.env, group))
            except Exception:
                ik_joints = []
            if len(ik_joints) == len(self.scene_joints):
                for sj, ij in zip(reversed(self.scene_joints), ik_joints):
                    mapping[sj] = int(ij)
        return mapping

    # ── 求解 ────────────────────────────────────────────────

    def solve(self, target_pose: list[float]) -> IKResult:
        """求解目标位姿, 返回 6 个**场景关节**的目标角度。"""
        if not self.initialized:
            return IKResult(False, message="IK 未初始化")

        self.sim.setObjectPose(self.target_handle, list(target_pose),
                               self.sim.handle_world)

        result = self._try_group(self.group_main, "主组(伪逆)")
        if result.success:
            return result

        backup = self._try_group(self.group_backup, "备用组(阻尼最小二乘)")
        backup.used_backup = True
        if backup.success:
            return backup

        # 连续任务时机械臂可能停在一个奇异构型, 当前状态出发会卡在局部极小。
        # 用固定 seed 作为 IK 初值再解一次 (实际关节仍由速度限幅的关节目标驱动)。
        if hasattr(self.simIK, "setJointPosition"):
            seeded_main = self._try_group(self.group_main, "主组(seed)",
                                          seed=cfg.ARM_SEED_JOINTS)
            if seeded_main.success:
                return seeded_main
            seeded_backup = self._try_group(
                self.group_backup, "备用组(seed)", seed=cfg.ARM_SEED_JOINTS)
            seeded_backup.used_backup = True
            if seeded_backup.success:
                return seeded_backup
            backup = seeded_backup

        return IKResult(
            False, [], backup.result_code, backup.reason, backup.precision,
            True,
            f"IK 不可达: 主组与备用组均失败 (线误差 {_lin(backup.precision):.4f} m, "
            f"角误差 {_ang(backup.precision):.4f} rad)")

    def _try_group(self, group: int, label: str,
                   seed: list[float] | None = None,
                   lock: tuple[int, float, float] | None = None) -> IKResult:
        sim, ik = self.sim, self.simIK
        ik.syncFromSim(self.env, [group])
        if seed is not None:
            mapping = self._maps.get(group, {})
            for sj, q in zip(self.scene_joints, seed):
                ij = mapping.get(sj)
                if ij is not None:
                    try:
                        ik.setJointPosition(self.env, ij, float(q))
                    except Exception:
                        break
        restore = self._apply_joint_lock(group, lock)
        try:
            ret = ik.handleGroup(self.env, group, {})
        finally:
            self._restore_joint_lock(restore)
        result_code, reason, precision = _parse_handle_result(ret)

        if result_code != ik.result_success:
            return IKResult(False, [], result_code, reason, precision, False,
                            f"{label} 求解失败 (result={result_code}, "
                            f"reason={reason})",
                            used_seed=seed is not None)

        joints = self._read_group_joint_positions(group)
        if joints is None:
            return IKResult(False, [], result_code, reason, precision, False,
                            f"{label} 求解成功但无法读取 6 个关节角",
                            used_seed=seed is not None)
        return IKResult(True, joints, result_code, reason, precision, False,
                        f"{label} 求解成功", used_seed=seed is not None)

    # ── IK 环境关节区间 (阶段 2.01 段内固定单轴) ──────────────

    def _apply_joint_lock(self, group: int,
                          lock: tuple[int, float, float] | None):
        """临时收窄 IK 关节区间; 返回恢复所需 ``(ik_joint, cyclic, interval)``."""
        if lock is None or not hasattr(self.simIK, "setJointInterval"):
            return None
        scene_joint, value, tol = lock
        ik_joint = self._maps.get(group, {}).get(int(scene_joint))
        if ik_joint is None:
            return None
        try:
            cyclic, interval = self.simIK.getJointInterval(
                self.env, int(ik_joint))
            half = max(float(tol), 1e-6)
            self.simIK.setJointInterval(
                self.env, int(ik_joint), False,
                [float(value) - half, 2.0 * half])
            return (int(ik_joint), bool(cyclic), list(interval))
        except Exception:
            return None

    def _restore_joint_lock(self, restore) -> None:
        if restore is None:
            return
        ik_joint, cyclic, interval = restore
        try:
            self.simIK.setJointInterval(self.env, ik_joint, cyclic, interval)
        except Exception:
            pass

    def solve_candidates(self, target_pose: list[float],
                         seeds: list[list[float]],
                         max_candidates: int = 8,
                         dedupe_tol: float = 0.05,
                         lock_joint: int | None = None,
                         lock_value: float | None = None,
                         lock_tol: float | None = None) -> list[IKResult]:
        """用给定初值枚举同一目标位姿的多个 IK 解 (阶段二多解规划用).

        - 只写 IK 环境的关节初值, 不写场景关节;
        - 每个 seed 先主组伪逆, 失败再阻尼备用组;
        - ``lock_joint`` 非空时临时把该 IK 关节区间收窄到
          ``lock_value ± lock_tol``, 求解后恢复原区间; 这是段内固定单轴,
          不修改场景关节限位;
        - 返回成功且关节在限位内的解, 按 q 距离去重;
        - 默认阶段一调用路径 (``solve``) 完全不变。
        """
        if not self.initialized:
            return []
        self.sim.setObjectPose(self.target_handle, list(target_pose),
                               self.sim.handle_world)
        out: list[IKResult] = []
        max_candidates = max(1, int(max_candidates))
        lock = None
        if lock_joint is not None and lock_value is not None:
            lock = (int(lock_joint), float(lock_value),
                    float(lock_tol if lock_tol is not None else 0.01))
        for seed in seeds:
            values = [float(v) for v in seed]
            if len(values) != len(self.scene_joints):
                continue
            result = self._try_group(self.group_main, "主组(seed)",
                                     seed=values, lock=lock)
            if not result.success:
                result = self._try_group(
                    self.group_backup, "备用组(seed)", seed=values, lock=lock)
                result.used_backup = True
            if not result.success:
                continue
            if lock is not None and abs(
                    result.joints[lock[0]] - lock[1]) > lock[2] + 1e-6:
                continue
            if not self.joints_within_limits(result.joints):
                continue
            if any(_joint_distance(result.joints, other.joints) <= dedupe_tol
                   for other in out):
                continue
            out.append(result)
            if len(out) >= max_candidates:
                break
        return out

    def _read_group_joint_positions(self, group: int) -> list[float] | None:
        mapping = self._maps.get(group, {})
        values: list[float] = []
        for sj in self.scene_joints:
            ij = mapping.get(sj)
            if ij is None:
                return None
            try:
                values.append(float(self.simIK.getJointPosition(self.env, ij)))
            except Exception:
                return None
        return values

    # ── 下发 / 校验 ─────────────────────────────────────────

    def apply_joints(self, joints: list[float],
                     motion: list[float] | None = None) -> None:
        if len(joints) != len(self.scene_joints):
            raise IKError(
                f"关节数不匹配: {len(joints)} != {len(self.scene_joints)}")
        if motion is None:
            motion = [cfg.ARM_JOINT_TARGET_VEL,
                      cfg.ARM_JOINT_TARGET_ACCEL,
                      cfg.ARM_JOINT_TARGET_JERK]
        for j, q in zip(self.scene_joints, joints):
            self.sim.setJointTargetPosition(j, float(q), motion)

    def joint_positions(self) -> list[float]:
        return [float(self.sim.getJointPosition(j)) for j in self.scene_joints]

    def converged(self, command: list[float]) -> bool:
        actual = self.joint_positions()
        return all(abs(a - c) <= cfg.ARM_JOINT_CONVERGE_TOL
                   for a, c in zip(actual, command))

    def tcp_pose(self) -> list[float]:
        return list(self.sim.getObjectPose(self.tcp_handle,
                                           self.sim.handle_world))

    def joints_within_limits(self, joints: list[float] | None = None) -> bool:
        vals = self.joint_positions() if joints is None else list(joints)
        low, high = cfg.ARM_JOINT_LIMIT
        return all(low - 1e-6 <= float(q) <= high + 1e-6 for q in vals)

    def joint_speed(self) -> float:
        """本体六轴的最大绝对角速度 (rad/s)。"""
        speeds = []
        for j in self.scene_joints:
            try:
                v = self.sim.getJointVelocity(j)
                if isinstance(v, (tuple, list)):
                    v = v[0]
                speeds.append(abs(float(v)))
            except Exception:
                return 0.0
        return max(speeds) if speeds else 0.0

    def stopped(self) -> bool:
        return self.joint_speed() <= cfg.ARM_JOINT_STOP_SPEED

    def tcp_speed(self) -> float:
        """TCP 当前线速度 (m/s) — 吸附前工具必须停稳。"""
        try:
            v = self.sim.getObjectVelocity(self.tcp_handle)
            lin = v[0] if isinstance(v, (tuple, list)) and len(v) == 2                 else list(v)[:3]
            return math.sqrt(sum(float(c) * float(c) for c in lin[:3]))
        except Exception:
            return 0.0

    def tcp_position_error(self, target_pose) -> float:
        p = self.sim.getObjectPosition(self.tcp_handle, self.sim.handle_world)
        return math.dist(list(p), list(target_pose[:3]))

    def tcp_orientation_error(self, target_pose) -> float:
        q = self.sim.getObjectQuaternion(self.tcp_handle, self.sim.handle_world)
        q_target = list(target_pose[3:7]) if len(target_pose) >= 7 \
            else list(cfg.TOOL_TARGET_QUATERNION)
        dot = abs(sum(float(q[i]) * q_target[i] for i in range(4)))
        dot = max(-1.0, min(1.0, dot))
        return 2.0 * math.acos(dot)

    def reached(self, target_pose) -> bool:
        return (self.tcp_position_error(target_pose) <= cfg.ARM_TCP_POS_TOL
                and self.tcp_orientation_error(target_pose) <= cfg.ARM_TCP_ANG_TOL)

    def reachable(self, point) -> bool:
        """构建期粗筛: 基座到目标点的直线距离。"""
        return cfg.arm_reach_to(point) <= cfg.REACH_LIMIT


def _parse_handle_result(ret) -> tuple[int, int, list[float]]:
    if isinstance(ret, (tuple, list)) and len(ret) >= 3:
        return int(ret[0]), int(ret[1]), list(ret[2])
    return int(ret), 0, []


def _joint_distance(a, b) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _lin(precision) -> float:
    return float(precision[0]) if precision else 0.0


def _ang(precision) -> float:
    return float(precision[1]) if len(precision) > 1 else 0.0
