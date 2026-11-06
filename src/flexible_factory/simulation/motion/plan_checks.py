"""plan_checks.py — 单构型与路径段检查 (ConfigurationChecker).

从 :mod:`path_planning` 拆出; 旧模块保留 re-export. 检查序列、容差与
预算行为保持不变.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .. import config_base as base
from .. import config_motion as cfg
from .branch import branch_ok
from .collisions import QueryState
from .contact import (ContactLimits, evaluate_contact,
                      quat_angle_between)
from .plan_types import (ConfigCheck, MotionContext, PlanBudget,
                         PlanBudgetExceeded, PlanStatus, SegmentCheck)


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

    def set_budget(self, budget: Optional[PlanBudget]) -> None:
        """设置/清除预算.

        ``LocalPlanner.plan`` 每次调用都会把本次规划预算挂到 checker 上;
        规划结束进入执行后必须清除 (或换回长驻预算), 否则执行期逐点复核
        会继续消耗规划墙钟, 长轨迹会被误判为规划预算超限.
        """
        self.budget = budget

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
        # 阶段 2.01: 腕部分支是硬约束; 近奇异/跨支构型在碰撞查询前拒绝,
        # 执行器逐步复核也走同一入口.
        if context.branch is not None:
            ok, label, margin = branch_ok(
                values, context.branch, context.branch_margin)
            if not ok:
                return ConfigCheck(
                    PlanStatus.WRIST_BRANCH_MISMATCH, values,
                    message=f"腕部分支 {label} 与锚点 {context.branch} "
                            f"不一致或裕量 {margin:.3f} < "
                            f"{context.branch_margin:.3f} rad")
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
            if check0.status is PlanStatus.QUERY_UNKNOWN:
                status = PlanStatus.QUERY_UNKNOWN
            elif check0.status is PlanStatus.WRIST_BRANCH_MISMATCH:
                status = PlanStatus.WRIST_BRANCH_MISMATCH
            else:
                status = PlanStatus.START_CONFIG_INVALID
            return SegmentCheck(status,
                                start, end, samples=1, first_failure=check0,
                                message=f"起点无效: {check0.message}")
        check1 = self.check(end, context, fast=fast,
                            allow_target_contact=allow_target_contact,
                            final_contact=final_contact,
                            collect_links=not fast, screen=screen,
                            allow_load_contact=allow_end_load_contact)
        if not check1.ok:
            if check1.status is PlanStatus.QUERY_UNKNOWN:
                status = PlanStatus.QUERY_UNKNOWN
            elif check1.status is PlanStatus.WRIST_BRANCH_MISMATCH:
                status = PlanStatus.WRIST_BRANCH_MISMATCH
            else:
                status = PlanStatus.GOAL_CONFIG_INVALID
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
