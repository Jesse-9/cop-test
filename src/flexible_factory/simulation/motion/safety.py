"""safety.py — v1 运动层运行期联锁与现场锁存.

本模块只维护**状态与判据**, 不直接下发设备命令:
  - ``SafetyObservation``: 一步的真实 q/TCP/物料位姿与期望值;
  - ``SafetyLatch``: 风险原因、命令/确认分离的停止证据;
  - ``request_stop(reason)``: 锁存并取消后续动作, 不自动恢复.

控制器负责在锁存后取消轨迹、停带/停车、保持最后实测构型, 并读取真实
速度确认停止。未知读数一律记为“停止未确认”, 不允许继续抓取 (06-09 §7.4).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .. import config_motion as cfg


@dataclass
class SafetyObservation:
    sim_time: float
    phase: str
    actual_q: list[float]
    commanded_q: Optional[list[float]] = None
    tcp_pose: Optional[list[float]] = None
    expected_tcp: Optional[list[float]] = None
    material_pose: Optional[list[float]] = None
    material_expected: Optional[list[float]] = None
    attached: bool = False
    query_state: str = "OK"
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "sim_time": round(float(self.sim_time), 4),
            "phase": self.phase,
            "actual_q": [round(float(v), 6) for v in self.actual_q],
            "commanded_q": (None if self.commanded_q is None else
                            [round(float(v), 6) for v in self.commanded_q]),
            "tcp_pose": (None if self.tcp_pose is None else
                         [round(float(v), 6) for v in self.tcp_pose]),
            "expected_tcp": (None if self.expected_tcp is None else
                             [round(float(v), 6) for v in self.expected_tcp]),
            "material_pose": (None if self.material_pose is None else
                              [round(float(v), 6)
                               for v in self.material_pose]),
            "material_expected": (None if self.material_expected is None else
                                  [round(float(v), 6)
                                   for v in self.material_expected]),
            "attached": self.attached,
            "query_state": self.query_state,
            "details": self.details,
        }


@dataclass
class SafetyLatch:
    engaged: bool = False
    code: str = ""
    reason: str = ""
    sim_time: Optional[float] = None
    details: dict = field(default_factory=dict)
    stop_commanded: bool = False
    stop_command_ok: Optional[bool] = None
    stop_confirmed: bool = False
    stop_checks: int = 0
    stop_speed: Optional[float] = None
    stop_source: str = ""

    def as_dict(self) -> dict:
        return {
            "engaged": self.engaged,
            "code": self.code,
            "reason": self.reason,
            "sim_time": self.sim_time,
            "details": self.details,
            "stop_commanded": self.stop_commanded,
            "stop_command_ok": self.stop_command_ok,
            "stop_confirmed": self.stop_confirmed,
            "stop_checks": self.stop_checks,
            "stop_speed": (None if self.stop_speed is None
                           else round(self.stop_speed, 6)),
            "stop_source": self.stop_source,
        }


class Stage2Safety:
    def __init__(self, logger=None) -> None:
        self.logger = logger
        self.latch = SafetyLatch()
        self.observations: list[SafetyObservation] = []

    # ── 判据 ────────────────────────────────────────────────

    @property
    def engaged(self) -> bool:
        return self.latch.engaged

    def evaluate(self, observation: SafetyObservation) -> list[str]:
        """返回违反项; 未知读数不是“安全”. 不修改锁存."""
        violations: list[str] = []
        obs = observation
        if obs.query_state not in ("OK", "CLEAR"):
            violations.append(f"QUERY_UNKNOWN({obs.query_state})")
        moving = bool(obs.details.get("moving", False))
        if obs.commanded_q is not None:
            try:
                deviation = max(abs(float(a) - float(c)) for a, c
                                in zip(obs.actual_q, obs.commanded_q))
                if deviation > cfg.EXEC_MAX_DEVIATION:
                    violations.append(
                        f"JOINT_DEVIATION({deviation:.4f}>"
                        f"{cfg.EXEC_MAX_DEVIATION:.4f})")
            except Exception:
                violations.append("JOINT_DEVIATION_UNREADABLE")
        if moving:
            # 运动中的 TCP/载荷偏差由轨迹执行器按计划逐段复核; 这里只检查
            # 失步和未知读数, 避免把“命令尚未达到”误判为漂移.
            return violations
        if obs.commanded_q is None:
            # 运动开始前/过渡状态还没有命令目标, 不把缺失期望位姿当作故障;
            # 一旦有命令或处于保持态, 缺失位姿按未知处理.
            return violations
        if obs.tcp_pose is None or obs.expected_tcp is None:
            violations.append("TCP_UNREADABLE")
        else:
            drift = math.dist(obs.tcp_pose[:3], obs.expected_tcp[:3])
            if drift > cfg.SAFETY_TCP_DRIFT_TOL:
                violations.append(
                    f"TCP_DRIFT({drift:.4f}>{cfg.SAFETY_TCP_DRIFT_TOL})")
        if obs.attached:
            if obs.material_pose is None or obs.material_expected is None:
                violations.append("LOAD_POSE_UNREADABLE")
            else:
                drift = math.dist(obs.material_pose[:3],
                                  obs.material_expected[:3])
                if drift > cfg.SAFETY_POSE_DRIFT_TOL:
                    violations.append(
                        f"LOAD_DRIFT({drift:.4f}>"
                        f"{cfg.SAFETY_POSE_DRIFT_TOL})")
        return violations

    def request_stop(self, reason: str, *, code: str = cfg.SAFETY_LATCH_CODE,
                     sim_time: Optional[float] = None,
                     details: Optional[dict] = None) -> SafetyLatch:
        if self.latch.engaged:
            return self.latch
        self.latch = SafetyLatch(
            engaged=True, code=code, reason=str(reason),
            sim_time=sim_time, details=dict(details or {}))
        if self.logger:
            self.logger.error(
                "[SAFETY] 锁存 %s: %s (t=%s, details=%s)",
                self.latch.code, self.latch.reason, sim_time, details or {})
        return self.latch

    # ── 停止证据 ────────────────────────────────────────────

    def note_stop_command(self, ok: bool, *, sim_time: Optional[float] = None
                          ) -> None:
        self.latch.stop_commanded = True
        self.latch.stop_command_ok = bool(ok)
        if self.latch.sim_time is None:
            self.latch.sim_time = sim_time
        if not ok and self.logger:
            self.logger.error("[SAFETY] 停止命令下发失败")

    def confirm_stop(self, *, speed_known: bool, speed: Optional[float],
                     agv_stopped: bool = True,
                     source: str = "velocity") -> bool:
        """真实读数确认停止; 未知读数不算确认, 也不当作失败继续等待."""
        self.latch.stop_checks += 1
        if speed_known and speed is not None and math.isfinite(speed):
            self.latch.stop_speed = float(speed)
            self.latch.stop_source = source
            if abs(float(speed)) <= cfg.SAFETY_STOP_VEL_TOL and agv_stopped:
                self.latch.stop_confirmed = True
            return self.latch.stop_confirmed
        # 速度不可读时只记录, 由控制器决定是否使用独立位姿回退
        self.latch.stop_source = f"{source}_unknown"
        self.latch.stop_confirmed = False
        return False

    def confirm_stop_from_position(self, *, pad_still: bool,
                                   agv_stopped: bool = True) -> bool:
        """独立位移判据回退: 仅当速度不可读且 pad 连续静止时使用."""
        self.latch.stop_checks += 1
        self.latch.stop_source = "pad_displacement"
        if pad_still and agv_stopped:
            self.latch.stop_confirmed = True
        return self.latch.stop_confirmed

    def reset_for_test(self) -> None:
        """测试专用显式复位; 生产恢复流程属于阶段 2.1."""
        self.latch = SafetyLatch()
