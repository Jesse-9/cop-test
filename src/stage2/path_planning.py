"""path_planning.py — 构型/路径检查与有限局部规划 (06-09 §4–§5).

所有候选都在**影子臂**上检查, 不移动真实机械臂:
  - ``check_configuration``: 自碰撞 (显式非相邻对) + 工作区/安装平台/
    非目标物料/目标物料 + 近距裕量 + 必需的接触几何;
  - ``check_segment``: 关节空间采样 + TCP/连杆位移自适应细分; 起点/终点
    碰撞单独报告; 采样/墙钟预算耗尽返回 ``PLAN_BUDGET_EXCEEDED``, 不当作通过;
  - ``LocalPlanner``: 多解 IK + 直接路径 → HOME 中转 → 抬高中转 → 工具
    yaw 变体; 只有完整通过检查的 ``MotionPlan`` 才会交给执行器.

本模块不直接调用 CoppeliaSim; 通过 ``ShadowProbe``/``CollisionRuntime``
和 ``ik.solve_candidates`` 注入, 因此可用 fake 探针完整测试.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional, Protocol, Sequence

from stage1 import config as base

from . import config as cfg
from .collisions import QueryResult, QueryState
from .contact import (ContactEvaluation, ContactLimits, LocalBox,
                      evaluate_contact, quat_angle_between, quat_rotate,
                      rotate_about_axis)
from .trajectory import cartesian_poses, joint_distance, slerp


class PlanStatus(str, Enum):
    OK = "OK"
    DIRECT_OK = "DIRECT_OK"
    AVOIDED = "AVOIDED"
    START_CONFIG_INVALID = "START_CONFIG_INVALID"
    GOAL_CONFIG_INVALID = "GOAL_CONFIG_INVALID"
    IK_FAILED = "IK_FAILED"
    LIMITS_EXCEEDED = "LIMITS_EXCEEDED"
    COLLISION_AT_GOAL = "COLLISION_AT_GOAL"
    PATH_COLLISION = "PATH_COLLISION"
    NEAR_COLLISION = "NEAR_COLLISION"
    TARGET_CONTACT_VIOLATION = "TARGET_CONTACT_VIOLATION"
    QUERY_UNKNOWN = "QUERY_UNKNOWN"
    LOCAL_AVOIDANCE_FAILED = "LOCAL_AVOIDANCE_FAILED"
    PLAN_BUDGET_EXCEEDED = "PLAN_BUDGET_EXCEEDED"


_OK_STATUSES = frozenset({PlanStatus.OK, PlanStatus.DIRECT_OK,
                          PlanStatus.AVOIDED})
_FAILURE_PRIORITY = {
    PlanStatus.QUERY_UNKNOWN: 100,
    PlanStatus.TARGET_CONTACT_VIOLATION: 90,
    PlanStatus.PATH_COLLISION: 80,
    PlanStatus.GOAL_CONFIG_INVALID: 75,
    PlanStatus.START_CONFIG_INVALID: 70,
    PlanStatus.NEAR_COLLISION: 60,
    PlanStatus.LIMITS_EXCEEDED: 55,
    PlanStatus.IK_FAILED: 50,
    PlanStatus.PLAN_BUDGET_EXCEEDED: 40,
    PlanStatus.LOCAL_AVOIDANCE_FAILED: 30,
}


class PlanBudgetExceeded(RuntimeError):
    pass


class PlanBudget:
    """采样数与墙钟预算; 超限必须返回“验证未完成”, 不能截断当通过."""

    def __init__(self, wall_s: float = cfg.PLAN_WALL_BUDGET_S,
                 max_samples: int = cfg.PLAN_MAX_TOTAL_SAMPLES):
        self.started = time.monotonic()
        self.wall_s = float(wall_s)
        self.max_samples = int(max_samples)
        self.samples = 0
        self.configs = 0

    def tick(self, configs: int = 1) -> None:
        self.configs += int(configs)
        self.samples += int(configs)
        if self.samples > self.max_samples:
            raise PlanBudgetExceeded(
                f"采样数 {self.samples} > {self.max_samples}")
        if time.monotonic() - self.started > self.wall_s:
            raise PlanBudgetExceeded(
                f"墙钟 {time.monotonic() - self.started:.2f}s > {self.wall_s}s")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


# ═══════════════════════════════════════════════════════════════
# 上下文与结果
# ═══════════════════════════════════════════════════════════════

@dataclass
class MotionContext:
    phase: str
    target_handle: int = -1
    material_pose: Optional[list[float]] = None
    material_box: Optional[LocalBox] = None
    carrying: bool = False
    cup_radius: float = cfg.CUP_RADIUS_DESIGN
    snapshot: str = ""
    goal_pose: Optional[list[float]] = None
    allow_target_contact: bool = False
    final_contact: bool = False
    # 抬升起点: 已吸附物料仍与承载面正常接触, 只允许该起点跳过载荷检查
    allow_start_load_contact: bool = False
    allow_final_load_contact: bool = False

    def with_flags(self, **kwargs) -> "MotionContext":
        data = {
            "phase": self.phase,
            "target_handle": self.target_handle,
            "material_pose": self.material_pose,
            "material_box": self.material_box,
            "carrying": self.carrying,
            "cup_radius": self.cup_radius,
            "snapshot": self.snapshot,
            "goal_pose": self.goal_pose,
            "allow_target_contact": self.allow_target_contact,
            "final_contact": self.final_contact,
            "allow_start_load_contact": self.allow_start_load_contact,
            "allow_final_load_contact": self.allow_final_load_contact,
        }
        data.update(kwargs)
        return MotionContext(**data)

    def as_dict(self) -> dict:
        return {
            "phase": self.phase,
            "target_handle": self.target_handle,
            "carrying": self.carrying,
            "snapshot": self.snapshot,
        }


@dataclass
class Goal:
    name: str
    pose: list[float]
    cartesian: bool = False
    allow_target_contact: bool = False
    final_contact: bool = False
    strict_orientation: bool = False
    # 最终放置点: 载荷底面与承载面正常接触, 只允许该端点跳过载荷检查
    allow_final_load_contact: bool = False


@dataclass
class ConfigCheck:
    status: PlanStatus
    q: list[float]
    tcp: Optional[list[float]] = None
    link_poses: list[list[float]] = field(default_factory=list)
    results: list[QueryResult] = field(default_factory=list)
    contact: Optional[ContactEvaluation] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "q": [round(float(v), 6) for v in self.q],
            "tcp": None if self.tcp is None
            else [round(float(v), 6) for v in self.tcp[:3]],
            "results": [r.as_dict() for r in self.results],
            "contact": self.contact.as_dict() if self.contact else None,
            "message": self.message,
        }


@dataclass
class SegmentCheck:
    status: PlanStatus
    q_start: list[float]
    q_end: list[float]
    samples: int = 0
    first_failure: Optional[ConfigCheck] = None
    message: str = ""
    min_distance: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "samples": self.samples,
            "first_failure": (self.first_failure.as_dict()
                              if self.first_failure else None),
            "message": self.message,
            "min_distance": None if self.min_distance is None
            else round(self.min_distance, 6),
        }


@dataclass
class MotionSegment:
    name: str
    q_start: list[float]
    q_end: list[float]
    cartesian: bool = False
    samples: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "cartesian": self.cartesian,
            "q_start": [round(float(v), 6) for v in self.q_start],
            "q_end": [round(float(v), 6) for v in self.q_end],
            "samples": self.samples,
        }


@dataclass
class MotionPlan:
    status: PlanStatus
    waypoints: list[list[float]] = field(default_factory=list)
    segments: list[MotionSegment] = field(default_factory=list)
    contexts: list[MotionContext] = field(default_factory=list)
    message: str = ""
    samples: int = 0
    configs: int = 0
    wall_s: float = 0.0
    attempts: list[dict] = field(default_factory=list)
    goal_pose: Optional[list[float]] = None
    final_contact: bool = False
    allow_final_load_contact: bool = False
    strict_orientation: bool = False
    strategy: str = ""

    @property
    def ok(self) -> bool:
        return self.status in _OK_STATUSES

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "strategy": self.strategy,
            "waypoints": [[round(float(v), 6) for v in q]
                          for q in self.waypoints],
            "segments": [s.as_dict() for s in self.segments],
            "message": self.message,
            "samples": self.samples,
            "configs": self.configs,
            "wall_s": round(self.wall_s, 3),
            "attempts": self.attempts,
            "goal_pose": (None if self.goal_pose is None
                          else [round(float(v), 6) for v in self.goal_pose]),
            "final_contact": self.final_contact,
            "strict_orientation": self.strict_orientation,
        }


# ═══════════════════════════════════════════════════════════════
# 构型检查
# ═══════════════════════════════════════════════════════════════

class ConfigurationChecker:
    """把影子探针 + 碰撞集合 + 接触几何组合成单一检查入口."""

    def __init__(self, sim, probe, collisions,
                 limits: Optional[ContactLimits] = None,
                 cup_radius: float = cfg.CUP_RADIUS_DESIGN) -> None:
        self.sim = sim
        self.probe = probe
        self.collisions = collisions
        self.limits = limits or ContactLimits(
            max_gap=cfg.CONTACT_MAX_GAP,
            min_signed_distance=cfg.CONTACT_MIN_SIGNED_DISTANCE,
            max_lateral=cfg.CONTACT_MAX_LATERAL,
            max_normal_angle=cfg.CONTACT_MAX_NORMAL_ANGLE,
            face_tolerance=cfg.CONTACT_FACE_TOLERANCE,
            require_cup_coverage=cfg.CONTACT_REQUIRE_CUP_COVERAGE,
        )
        self.cup_radius = float(cup_radius)
        self.budget: Optional[PlanBudget] = None
        self.last_check: Optional[ConfigCheck] = None

    def with_budget(self, budget: PlanBudget) -> "ConfigurationChecker":
        self.budget = budget
        return self

    # ── 基础 ────────────────────────────────────────────────

    def set_configuration(self, q: Sequence[float]) -> Optional[str]:
        values = [float(v) for v in q]
        if len(values) != 6:
            return f"关节数不是 6: {len(values)}"
        if not all(math.isfinite(v) for v in values):
            return "构型含非有限值"
        low, high = base.ARM_JOINT_LIMIT
        bad = [v for v in values if v < low - 1e-6 or v > high + 1e-6]
        if bad:
            return f"关节超限: {bad}"
        self.probe.set_configuration(values)
        return None

    def tcp_at(self, q: Sequence[float]) -> Optional[list[float]]:
        error = self.set_configuration(q)
        if error is not None:
            return None
        try:
            return self.probe.tcp_pose()
        except Exception:
            return None

    # ── 单构型 ──────────────────────────────────────────────

    def check(self, q: Sequence[float], context: MotionContext,
              allow_target_contact: Optional[bool] = None,
              final_contact: Optional[bool] = None,
              fast: bool = False,
              collect_links: bool = True,
              check_self: bool = True,
              screen: bool = False,
              allow_load_contact: bool = False) -> ConfigCheck:
        values = [float(v) for v in q]
        error = self.set_configuration(values)
        if error is not None:
            return ConfigCheck(PlanStatus.LIMITS_EXCEEDED, values,
                               message=error)
        try:
            tcp = self.probe.tcp_pose()
            links = (self.probe.key_link_poses()
                     if (collect_links and not screen) else [])
            load_pose = self.probe.load_pose() if context.carrying else None
        except Exception as exc:
            return ConfigCheck(PlanStatus.QUERY_UNKNOWN, values,
                               message=f"影子 FK 读取失败: {exc}")

        allow = (context.allow_target_contact if allow_target_contact is None
                 else bool(allow_target_contact))
        final = (bool(getattr(context, "final_contact", False))
                 if final_contact is None else bool(final_contact))
        goal_pose = context.goal_pose

        target_active = (context.target_handle >= 0
                         and context.material_pose is not None
                         and context.material_box is not None)
        contact = None
        if target_active:
            try:
                contact = evaluate_contact(
                    tcp, context.material_pose, context.material_box,
                    self.limits, context.cup_radius, uid=None)
            except Exception as exc:
                return ConfigCheck(PlanStatus.TARGET_CONTACT_VIOLATION,
                                   values, tcp=tcp, link_poses=links,
                                   message=f"接触几何计算失败: {exc}")

        margin = None if (fast or screen
                          or context.phase in cfg.CONTACT_PHASES) else \
            cfg.COLLISION_MARGIN
        try:
            results = self.collisions.probe_configuration(
                check_target=target_active and not context.carrying,
                allow_target_contact=allow,
                check_load=bool(context.carrying and not allow_load_contact),
                margin=margin,
                check_self=bool(check_self and not fast and not screen))
        except Exception as exc:
            return ConfigCheck(PlanStatus.QUERY_UNKNOWN, values, tcp=tcp,
                               link_poses=links,
                               message=f"碰撞查询异常: {exc}")
        if self.budget is not None:
            try:
                self.budget.tick(1)
            except PlanBudgetExceeded as exc:
                raise exc

        unknown = [r for r in results if r.state is QueryState.UNKNOWN]
        if unknown:
            return ConfigCheck(PlanStatus.QUERY_UNKNOWN, values, tcp=tcp,
                               link_poses=links, results=results,
                               contact=contact,
                               message=f"查询未知: {unknown[0].message}")
        collision = [r for r in results if r.state is QueryState.COLLISION]
        for result in collision:
            if result.name == "load_vs_workcell" and allow_load_contact:
                # 只放行载荷与承载面的端点接触; 载荷-非目标物料仍需拒绝
                continue
            if (result.name == "pad_vs_target" and allow
                    and contact is not None and contact.valid):
                continue
            if result.name == "links_vs_target":
                return ConfigCheck(PlanStatus.TARGET_CONTACT_VIOLATION,
                                   values, tcp=tcp, link_poses=links,
                                   results=results, contact=contact,
                                   message=f"非杯口部位与目标物料碰撞: "
                                           f"{result.pair}")
            if result.name in ("pad_vs_target",):
                return ConfigCheck(PlanStatus.TARGET_CONTACT_VIOLATION,
                                   values, tcp=tcp, link_poses=links,
                                   results=results, contact=contact,
                                   message="杯口接触不满足表面几何判据")
            return ConfigCheck(PlanStatus.PATH_COLLISION, values, tcp=tcp,
                               link_poses=links, results=results,
                               contact=contact,
                               message=f"{result.name} 碰撞对 {result.pair} "
                                       f"{result.pair_aliases} "
                                       f"load={None if load_pose is None else [round(v, 4) for v in load_pose[:3]]}")
        near = [r for r in results if r.state is QueryState.NEAR]
        if near:
            return ConfigCheck(PlanStatus.NEAR_COLLISION, values, tcp=tcp,
                               link_poses=links, results=results,
                               contact=contact,
                               message=f"{near[0].name} 距离 "
                                       f"{near[0].distance:.4f} m < "
                                       f"{near[0].threshold} m")
        if final and target_active:
            if contact is None or not contact.valid:
                reasons = contact.reasons if contact else ("missing",)
                return ConfigCheck(PlanStatus.TARGET_CONTACT_VIOLATION,
                                   values, tcp=tcp, link_poses=links,
                                   results=results, contact=contact,
                                   message=f"最终接触构型不满足表面判据: "
                                           f"{list(reasons)}")
            if goal_pose is not None:
                pos_err = math.dist(tcp[:3], [float(v)
                                              for v in goal_pose[:3]])
                ang_err = quat_angle_between(tcp[3:7], goal_pose[3:7])
                if pos_err > cfg.EXEC_TCP_POS_TOL or \
                        ang_err > cfg.EXEC_TCP_ANG_TOL:
                    return ConfigCheck(PlanStatus.GOAL_CONFIG_INVALID, values,
                                       tcp=tcp, link_poses=links,
                                       results=results, contact=contact,
                                       message=f"FK 未到达目标: pos={pos_err:.4f}"
                                               f" ang={ang_err:.4f}")
        return ConfigCheck(PlanStatus.OK, values, tcp=tcp,
                           link_poses=links, results=results,
                           contact=contact)

    # ── 路径段 ──────────────────────────────────────────────

    def check_segment(self, q0: Sequence[float], q1: Sequence[float],
                      context: MotionContext, *,
                      fast: bool = False,
                      samples: Optional[int] = None,
                      allow_target_contact: Optional[bool] = None,
                      final_contact: Optional[bool] = None,
                      cartesian: bool = False,
                      screen: bool = False,
                      allow_start_load_contact: bool = False,
                      allow_end_load_contact: bool = False
                      ) -> SegmentCheck:
        start = [float(v) for v in q0]
        end = [float(v) for v in q1]
        collect_links = bool(not fast and not cartesian and not screen)
        check0 = self.check(start, context, fast=fast,
                            allow_target_contact=allow_target_contact,
                            collect_links=not fast, screen=screen,
                            allow_load_contact=allow_start_load_contact)
        if not check0.ok:
            status = (PlanStatus.QUERY_UNKNOWN
                      if check0.status is PlanStatus.QUERY_UNKNOWN
                      else PlanStatus.START_CONFIG_INVALID)
            return SegmentCheck(status,
                                start, end, samples=1, first_failure=check0,
                                message=f"起点无效: {check0.message}")
        check1 = self.check(end, context, fast=fast,
                            allow_target_contact=allow_target_contact,
                            final_contact=final_contact,
                            collect_links=not fast, screen=screen,
                            allow_load_contact=allow_end_load_contact)
        if not check1.ok:
            status = (PlanStatus.QUERY_UNKNOWN
                      if check1.status is PlanStatus.QUERY_UNKNOWN
                      else PlanStatus.GOAL_CONFIG_INVALID)
            return SegmentCheck(status,
                                start, end, samples=2, first_failure=check1,
                                message=f"终点无效: {check1.message}")

        max_delta = max(abs(a - b) for a, b in zip(start, end))
        delta = cfg.SCREEN_SAMPLE_MAX_DELTA if screen else \
            cfg.JOINT_SAMPLE_MAX_DELTA
        if samples is None:
            count = max(1, int(math.ceil(max_delta / delta)))
        else:
            count = max(1, int(samples) - 1)
        if count + 1 > cfg.SEGMENT_MAX_SAMPLES:
            raise PlanBudgetExceeded(
                f"路径段采样 {count + 1} > {cfg.SEGMENT_MAX_SAMPLES}")

        evaluated: dict[float, ConfigCheck] = {}
        points: list[float] = [i / count for i in range(count + 1)]
        depth: dict[float, int] = {t: 0 for t in points}
        for index, t in enumerate(points):
            if t == 0.0:
                evaluated[t] = check0
                continue
            if t == 1.0:
                evaluated[t] = check1
                continue
            check = self.check(
                _lerp(start, end, t), context, fast=fast,
                allow_target_contact=allow_target_contact,
                collect_links=collect_links,
                check_self=((not fast) and (not screen)
                            and index % cfg.SELF_CHECK_STRIDE == 0),
                screen=screen)
            evaluated[t] = check
            if not check.ok:
                return SegmentCheck(
                    check.status, start, end, samples=len(evaluated),
                    first_failure=check,
                    message=f"路径 t={t:.3f} 失败: {check.message}")
        idx = 0
        while idx < len(points) - 1:
            t0, t1 = points[idx], points[idx + 1]
            mid_t = (t0 + t1) / 2.0
            if mid_t in evaluated:
                idx += 1
                continue
            need = (not screen and not fast and
                    self._needs_subdivision(evaluated[t0], evaluated[t1],
                                            cartesian=cartesian))
            if need and depth[t0] < cfg.SEGMENT_MAX_SUBDIVISION and \
                    len(points) < cfg.SEGMENT_MAX_SAMPLES:
                mid = _lerp(start, end, mid_t)
                check = self.check(
                    mid, context, fast=fast,
                    allow_target_contact=allow_target_contact,
                    collect_links=not fast, check_self=not fast,
                    screen=screen)
                evaluated[mid_t] = check
                points.insert(idx + 1, mid_t)
                depth[mid_t] = depth[t0] + 1
                if not check.ok:
                    return SegmentCheck(
                        check.status, start, end, samples=len(evaluated),
                        first_failure=check,
                        message=f"路径中间构型失败 (t={mid_t:.3f}): "
                                f"{check.message}")
                continue
            idx += 1

        for t in sorted(evaluated):
            check = evaluated[t]
            if not check.ok:
                return SegmentCheck(check.status, start, end,
                                    samples=len(evaluated),
                                    first_failure=check,
                                    message=f"路径 t={t:.3f} 失败: "
                                            f"{check.message}")
        distances = [r.distance for c in evaluated.values()
                     for r in c.results if r.distance is not None]
        return SegmentCheck(PlanStatus.OK, start, end,
                            samples=len(evaluated),
                            min_distance=min(distances) if distances else None)

    @staticmethod
    def _needs_subdivision(a: ConfigCheck, b: ConfigCheck,
                           cartesian: bool = False) -> bool:
        if not a.tcp or not b.tcp:
            return False
        if cartesian:
            return math.dist(a.tcp[:3], b.tcp[:3]) > \
                cfg.CARTESIAN_SAMPLE_MAX_DELTA
        # 自由关节段: TCP 轨迹不是安全判据, 用关键连杆的实际位移
        if a.link_poses and b.link_poses:
            for pa, pb in zip(a.link_poses, b.link_poses):
                if math.dist(pa[:3], pb[:3]) > cfg.LINK_MOTION_MAX_DELTA:
                    return True
        return False


def _lerp(a: Sequence[float], b: Sequence[float], t: float) -> list[float]:
    return [float(a[i]) + (float(b[i]) - float(a[i])) * t
            for i in range(len(a))]


# ═══════════════════════════════════════════════════════════════
# 局部规划
# ═══════════════════════════════════════════════════════════════

class CandidateSolver(Protocol):
    def solve_candidates(self, pose, seeds, max_candidates=6,
                         dedupe_tol=0.05): ...


class LocalPlanner:
    """有上限的局部候选搜索; 不返回“部分路径也可执行”."""

    def __init__(self, checker: ConfigurationChecker, ik: CandidateSolver,
                 budget: Optional[PlanBudget] = None) -> None:
        self.checker = checker
        self.ik = ik
        self.budget = budget

    def set_budget(self, budget: Optional[PlanBudget]) -> None:
        self.budget = budget
        self.checker.budget = budget

    # ── 求解 ────────────────────────────────────────────────

    def _seeds(self, primary: Sequence[float],
               thorough: bool = False) -> list[list[float]]:
        seeds = [[float(v) for v in primary]]
        if not thorough:
            return seeds
        candidates: list[Sequence[float]] = list(cfg.IK_SEED_VARIANTS)
        candidates.append(base.ARM_SEED_JOINTS)
        for seed in candidates:
            values = [float(v) for v in seed]
            if len(values) != 6:
                continue
            if any(joint_distance(values, known) < 0.1 for known in seeds):
                continue
            seeds.append(values)
            if len(seeds) >= cfg.IK_MAX_SEEDS:
                break
        return seeds

    def solve_pose(self, pose: Sequence[float], primary: Sequence[float],
                   thorough: bool = False) -> list[list[float]]:
        """返回按关节连续性排序的候选解.

        ``thorough=True`` 只在自由关节段使用 (多 seed 找安全分支);
        法向接近段以上一解为 seed, 避免段内分支跳变.
        """
        seeds = self._seeds(primary, thorough=thorough)
        results = self.ik.solve_candidates(
            list(pose), seeds, max_candidates=cfg.IK_MAX_CANDIDATES,
            dedupe_tol=cfg.IK_DEDUPE_TOL)
        qs = [list(map(float, r.joints)) for r in results if r.success]
        reference = [float(v) for v in primary]

        def score(q):
            return (joint_distance(q, reference)
                    + cfg.IK_WRIST_PENALTY_WEIGHT * abs(_wrap_pi(q[5])))
        return sorted(qs, key=score)

    # ── 规划入口 ────────────────────────────────────────────

    def plan(self, start_q: Sequence[float], goals: Sequence[Goal],
             context: MotionContext,
             start_pose: Optional[Sequence[float]] = None
             ) -> MotionPlan:
        started = time.monotonic()
        budget = self.budget or PlanBudget()
        self.checker.budget = budget
        strategies = self._strategies(goals)
        attempts: list[dict] = []
        best: Optional[MotionPlan] = None
        exhausted = False
        for name, transit, goals_variant in strategies:
            try:
                plan = self._attempt(name, start_q, goals_variant, context,
                                     start_pose, transit, budget)
            except PlanBudgetExceeded as exc:
                plan = MotionPlan(
                    PlanStatus.PLAN_BUDGET_EXCEEDED,
                    message=f"规划预算耗尽: {exc}",
                    samples=budget.samples, configs=budget.configs)
                exhausted = True
            except Exception as exc:
                plan = MotionPlan(
                    PlanStatus.LOCAL_AVOIDANCE_FAILED,
                    message=f"{type(exc).__name__}: {exc}",
                    samples=budget.samples, configs=budget.configs)
            plan.strategy = name
            plan.wall_s = time.monotonic() - started
            plan.samples = budget.samples
            plan.configs = budget.configs
            if plan.ok:
                try:
                    verified = self._verify_plan(plan)
                except PlanBudgetExceeded as exc:
                    verified = None
                    exhausted = True
                    plan.status = PlanStatus.PLAN_BUDGET_EXCEEDED
                    plan.message = f"最终复核预算耗尽: {exc}"
                if verified is not None and verified.ok:
                    plan.message = (
                        f"{len(plan.segments)} 段通过粗筛与最终复核")
                    plan.attempts = attempts + [{
                        "strategy": name,
                        "status": plan.status.value,
                        "message": plan.message,
                    }]
                    return plan
                plan.status = verified.status
                plan.message = f"最终复核失败: {verified.message}"
            plan.attempts = attempts + [{
                "strategy": name,
                "status": plan.status.value,
                "message": plan.message,
            }]
            if best is None or (_FAILURE_PRIORITY.get(plan.status, 0)
                                > _FAILURE_PRIORITY.get(best.status, 0)):
                best = plan
            attempts.append({
                "strategy": name,
                "status": plan.status.value,
                "message": plan.message,
            })
            if exhausted or time.monotonic() - started > \
                    cfg.PLAN_WALL_BUDGET_S:
                break
        if best is None:
            best = MotionPlan(PlanStatus.LOCAL_AVOIDANCE_FAILED,
                              message="没有可用候选")
        best.attempts = attempts
        best.wall_s = time.monotonic() - started
        best.samples = budget.samples
        best.configs = budget.configs
        if best.status in _OK_STATUSES:
            best.status = PlanStatus.LOCAL_AVOIDANCE_FAILED
        return best

    def _verify_plan(self, plan: MotionPlan) -> SegmentCheck:
        """对完整计划按 2° 采样重新执行全部检查 (粗筛不能代替复核)."""
        last = len(plan.waypoints) - 1
        total = 0
        for i in range(last):
            ctx = plan.contexts[min(i + 1, len(plan.contexts) - 1)]
            check = self.checker.check_segment(
                plan.waypoints[i], plan.waypoints[i + 1], ctx,
                allow_target_contact=ctx.allow_target_contact,
                final_contact=bool(plan.final_contact and i + 1 == last),
                cartesian=False, screen=False,
                allow_start_load_contact=bool(
                    i == 0 and ctx.allow_start_load_contact),
                allow_end_load_contact=bool(
                    plan.allow_final_load_contact and i + 1 == last))
            total += check.samples
            if not check.ok:
                return check
        return SegmentCheck(
            PlanStatus.OK, plan.waypoints[0], plan.waypoints[-1],
            samples=total, message="最终复核通过")

    def _strategies(self, goals: Sequence[Goal]):
        """候选策略: 直接 → HOME 中转 → 抬高中转 → 工具 yaw 变体."""
        yield ("direct", None, list(goals))
        if goals:
            yield ("home_transit", ("joint", list(base.ARM_SEED_JOINTS)),
                   list(goals))
            first = goals[0]
            for height in cfg.PRE_APPROACH_HEIGHT_VARIANTS:
                for lateral in cfg.LATERAL_OFFSET_VARIANTS:
                    if height == cfg.PRE_APPROACH_CLEARANCE and lateral == 0.0:
                        continue
                    pose = list(first.pose)
                    pose[2] += float(height) - cfg.PRE_APPROACH_CLEARANCE
                    pose[1] += float(lateral)
                    yield (f"pose_transit_h{height:.2f}_y{lateral:+.2f}",
                           ("pose", pose), list(goals))
            for yaw in cfg.TOOL_YAW_VARIANTS:
                if abs(yaw) < 1e-9:
                    continue
                yield (f"yaw_{math.degrees(yaw):+.0f}",
                       None, _yaw_goals(goals, yaw))

    # ── 单个策略 ────────────────────────────────────────────

    def _attempt(self, name: str, start_q: Sequence[float],
                 goals: Sequence[Goal], context: MotionContext,
                 start_pose: Optional[Sequence[float]], transit,
                 budget: PlanBudget) -> MotionPlan:
        q_path = [[float(v) for v in start_q]]
        waypoint_contexts: list[MotionContext] = [context]
        segments: list[MotionSegment] = []
        current_pose = (list(start_pose) if start_pose is not None
                        else self.checker.tcp_at(q_path[0]))
        if current_pose is None:
            return MotionPlan(PlanStatus.START_CONFIG_INVALID,
                              message="无法读取起始 TCP 位姿")
        state = {"status": PlanStatus.LOCAL_AVOIDANCE_FAILED,
                 "message": "没有安全分支", "nodes": 0}

        if transit is not None:
            kind, payload = transit
            if kind == "joint":
                check = self.checker.check_segment(
                    q_path[-1], payload, context, screen=True)
                if not check.ok:
                    return MotionPlan(check.status,
                                      message=f"中转构型无效: {check.message}")
                q_path.append([float(v) for v in payload])
                waypoint_contexts.append(context)
                segments.append(MotionSegment("transit_joint", q_path[-2],
                                              q_path[-1]))
            else:
                candidates = self.solve_pose(payload, q_path[-1],
                                             thorough=True)
                chosen = self._first_safe(q_path[-1], candidates, context,
                                          goal_pose=payload)
                if chosen is None:
                    return MotionPlan(
                        PlanStatus.LOCAL_AVOIDANCE_FAILED,
                        message="中转位姿没有安全 IK 分支")
                q_path.append(chosen)
                waypoint_contexts.append(context)
                segments.append(MotionSegment("transit_pose", q_path[-2],
                                              chosen))
                current_pose = list(payload)

        def search(gi, q_prev, pose_prev, q_cur, ctx_cur, seg_cur):
            if gi >= len(goals):
                return q_cur, ctx_cur, seg_cur
            goal = goals[gi]
            is_last = goal is goals[-1]
            goal_ctx = context.with_flags(
                goal_pose=list(goal.pose),
                allow_target_contact=goal.allow_target_contact,
                allow_final_load_contact=goal.allow_final_load_contact)
            if goal.cartesian:
                ok, chain, status, message = self._cartesian_chain(
                    q_prev, pose_prev, goal, goal_ctx)
                if not ok:
                    state["status"], state["message"] = status, message
                    return None
                new_q = list(q_cur)
                new_ctx = list(ctx_cur)
                for q in chain[1:]:
                    new_q.append(q)
                    new_ctx.append(goal_ctx)
                new_seg = list(seg_cur)
                new_seg.append(MotionSegment(
                    goal.name, chain[-2] if len(chain) > 1 else chain[-1],
                    chain[-1], cartesian=True, samples=len(chain) - 1))
                return search(gi + 1, chain[-1], goal.pose, new_q, new_ctx,
                              new_seg)

            candidates = self.solve_pose(goal.pose, q_prev, thorough=True)
            for candidate in candidates[:cfg.PLAN_MAX_WAYPOINT_CANDIDATES]:
                state["nodes"] += 1
                if state["nodes"] > cfg.PLAN_MAX_DFS_NODES:
                    raise PlanBudgetExceeded("局部回溯节点超限")
                check = self.checker.check_segment(
                    q_prev, candidate, goal_ctx,
                    allow_target_contact=goal.allow_target_contact,
                    final_contact=goal.final_contact and is_last,
                    screen=True)
                if not check.ok:
                    state["status"], state["message"] =                         check.status, check.message
                    continue
                pos_err, ang_err = self._pose_error(
                    candidate, goal.pose, strict=goal.strict_orientation)
                if pos_err > cfg.EXEC_TCP_POS_TOL or                         ang_err > cfg.EXEC_TCP_ANG_TOL:
                    state["status"] = PlanStatus.GOAL_CONFIG_INVALID
                    state["message"] = (
                        f"IK 未到达 {goal.name}: pos={pos_err:.4f} "
                        f"ang={ang_err:.4f}")
                    continue
                final = self.checker.check(
                    candidate, goal_ctx,
                    allow_target_contact=goal.allow_target_contact,
                    final_contact=goal.final_contact and is_last)
                if not final.ok:
                    state["status"], state["message"] =                         final.status, final.message
                    continue
                new_q = list(q_cur) + [[float(v) for v in candidate]]
                new_ctx = list(ctx_cur) + [goal_ctx]
                new_seg = list(seg_cur) + [
                    MotionSegment(goal.name, q_prev, candidate)]
                result = search(gi + 1, candidate, goal.pose, new_q,
                                new_ctx, new_seg)
                if result is not None:
                    return result
            return None

        result = search(0, q_path[-1], current_pose, q_path,
                        waypoint_contexts, segments)
        if result is None:
            return MotionPlan(state["status"], message=state["message"])
        final_q, final_ctx, final_segs = result
        status = PlanStatus.DIRECT_OK if name == "direct" else PlanStatus.AVOIDED
        return MotionPlan(status, waypoints=final_q, segments=final_segs,
                          contexts=final_ctx,
                          goal_pose=list(goals[-1].pose) if goals else None,
                          final_contact=(bool(goals[-1].final_contact)
                                         if goals else False),
                          allow_final_load_contact=(
                              bool(goals[-1].allow_final_load_contact)
                              if goals else False),
                          strict_orientation=(
                              bool(goals[-1].strict_orientation)
                              if goals else False),
                          message=f"{len(final_segs)} 段通过粗筛, 待最终复核")

    def _cartesian_chain(self, start_q, start_pose, goal: Goal,
                         context: MotionContext):
        """从 start 位姿沿直线到 goal; 返回 (ok, q_chain, status, message)."""
        poses = cartesian_poses(start_pose, goal.pose,
                                cfg.CARTESIAN_IK_STEP)
        chain = [[float(v) for v in start_q]]
        pose_prev = list(start_pose)
        last_status, last_message = PlanStatus.IK_FAILED, "IK 无解"
        for index, pose in enumerate(poses[1:], start=1):
            candidates = self.solve_pose(pose, chain[-1], thorough=False)
            chosen = None
            for candidate in candidates:
                deviation = self._cartesian_deviation(
                    chain[-1], candidate, pose_prev, pose)
                if not deviation["ok"]:
                    last_status = PlanStatus.GOAL_CONFIG_INVALID
                    last_message = (
                        f"法向插值偏差 lateral={deviation['lateral']:.4f} m "
                        f"ang={deviation['angle']:.4f} rad")
                    continue
                check = self.checker.check_segment(
                    chain[-1], candidate, context,
                    allow_target_contact=goal.allow_target_contact,
                    final_contact=(goal.final_contact
                                   and index == len(poses) - 1),
                    cartesian=False, screen=True,
                    allow_start_load_contact=(
                        context.allow_start_load_contact and index == 1),
                    allow_end_load_contact=(
                        goal.allow_final_load_contact
                        and index == len(poses) - 1))
                if check.ok:
                    pos_err, ang_err = self._pose_error(
                        candidate, pose,
                        strict=(goal.strict_orientation
                                and index == len(poses) - 1))
                    if pos_err <= cfg.EXEC_TCP_POS_TOL and \
                            ang_err <= cfg.EXEC_TCP_ANG_TOL:
                        chosen = candidate
                        break
                    last_status = PlanStatus.GOAL_CONFIG_INVALID
                    last_message = (f"IK 未到达法向点: pos={pos_err:.4f} "
                                    f"ang={ang_err:.4f}")
                    continue
                last_status, last_message = check.status, check.message
            if chosen is None:
                return (False, chain, last_status,
                        f"法向段 {goal.name} 第 {index} 点失败: {last_message}")
            chain.append(chosen)
            pose_prev = list(pose)
        final = self.checker.check(
            chain[-1], context,
            allow_target_contact=goal.allow_target_contact,
            final_contact=goal.final_contact,
            allow_load_contact=goal.allow_final_load_contact)
        if not final.ok:
            return (False, chain, final.status,
                    f"目标 {goal.name}: {final.message}")
        return (True, chain, PlanStatus.OK,
                f"{len(chain) - 1} 个法向点通过")

    def _cartesian_deviation(self, q0, q1, pose0, pose1) -> dict:
        """FK 复核关节插值相对笛卡尔直线/姿态的偏差."""
        start = [float(v) for v in pose0]
        goal = [float(v) for v in pose1]
        length = math.dist(start[:3], goal[:3])
        if length < 1e-9:
            return {"ok": True, "lateral": 0.0, "angle": 0.0}
        direction = [(goal[i] - start[i]) / length for i in range(3)]
        n = max(2, cfg.CARTESIAN_VERIFY_SAMPLES)
        lateral = 0.0
        angle = 0.0
        for i in range(1, n):
            t = i / n
            q = [float(q0[j]) + (float(q1[j]) - float(q0[j])) * t
                 for j in range(len(q0))]
            tcp = self.checker.tcp_at(q)
            if tcp is None:
                return {"ok": False, "lateral": float("inf"),
                        "angle": float("inf"), "error": "fk_unreadable"}
            rel = [tcp[k] - start[k] for k in range(3)]
            along = sum(rel[k] * direction[k] for k in range(3))
            perp = math.sqrt(max(0.0, sum(v * v for v in rel) - along * along))
            lateral = max(lateral, perp)
            if along < -cfg.CARTESIAN_PATH_POS_TOL or \
                    along > length + cfg.CARTESIAN_PATH_POS_TOL:
                lateral = max(lateral, abs(along) + abs(along - length))
            expected_q = slerp(start[3:7], goal[3:7],
                               max(0.0, min(1.0, along / length)))
            # 只约束工具 Z 轴 (法向); 绕法向 yaw 在接近段是允许的
            axis_tcp = quat_rotate(tcp[3:7], (0.0, 0.0, 1.0))
            axis_expected = quat_rotate(expected_q, (0.0, 0.0, 1.0))
            angle = max(angle, math.acos(max(-1.0, min(
                1.0, sum(a * b for a, b in zip(axis_tcp, axis_expected))))))
        return {
            "ok": (lateral <= cfg.CARTESIAN_PATH_POS_TOL
                   and angle <= cfg.CARTESIAN_PATH_ANG_TOL),
            "lateral": lateral,
            "angle": angle,
        }

    def _pose_error(self, q: Sequence[float],
                    pose: Sequence[float],
                    strict: bool = True) -> tuple[float, float]:
        """FK 检查候选构型是否真的到达目标位姿.

        ``strict=False`` 只比较工具 Z 轴 (法向), 允许绕法向的 yaw; 圆形
        吸盘和方形物料顶面都不依赖 yaw, 但最终放置位姿仍用 strict 检查.
        """
        tcp = self.checker.tcp_at(q)
        if tcp is None:
            return float("inf"), float("inf")
        pos = math.dist(tcp[:3], [float(v) for v in pose[:3]])
        if strict:
            ang = quat_angle_between(tcp[3:7], pose[3:7])
        else:
            axis_tcp = quat_rotate(tcp[3:7], (0.0, 0.0, 1.0))
            axis_goal = quat_rotate(pose[3:7], (0.0, 0.0, 1.0))
            dot = max(-1.0, min(1.0, sum(a * b for a, b in
                                         zip(axis_tcp, axis_goal))))
            ang = math.acos(dot)
        return pos, ang

    def _first_safe(self, start_q: Sequence[float],
                    candidates: Sequence[Sequence[float]],
                    context: MotionContext,
                    goal_pose: Optional[Sequence[float]] = None
                    ) -> Optional[list[float]]:
        for candidate in candidates:
            check = self.checker.check_segment(start_q, candidate, context,
                                               screen=True)
            if not check.ok:
                continue
            if goal_pose is not None:
                pos_err, ang_err = self._pose_error(candidate, goal_pose)
                if pos_err > cfg.EXEC_TCP_POS_TOL or \
                        ang_err > cfg.EXEC_TCP_ANG_TOL:
                    continue
            return [float(v) for v in candidate]
        return None


def _yaw_goals(goals: Sequence[Goal], yaw: float) -> list[Goal]:
    out = []
    for goal in goals:
        pose = list(goal.pose)
        pose[3:7] = rotate_about_axis(pose[3:7], (0.0, 0.0, 1.0), yaw)
        out.append(Goal(goal.name, pose, goal.cartesian,
                        goal.allow_target_contact, goal.final_contact,
                        goal.strict_orientation,
                        goal.allow_final_load_contact))
    return out


def _wrap_pi(angle: float) -> float:
    value = math.remainder(float(angle), 2.0 * math.pi)
    if value <= -math.pi:
        value += 2.0 * math.pi
    return value
