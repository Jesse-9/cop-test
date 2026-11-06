"""plan_search.py — 有限局部候选搜索 (LocalPlanner).

从 :mod:`path_planning` 拆出; 旧模块保留 re-export. 候选顺序、预算、
安全检查与计划后处理调用顺序保持不变.
"""

from __future__ import annotations

import math
import time
from typing import Optional, Protocol, Sequence

from .. import config_base as base
from .. import config_motion as cfg
from . import diagnostics as motion_diagnostics
from . import metrics as motion_metrics
from .branch import wrist_branch
from .contact import quat_angle_between, quat_rotate, rotate_about_axis
from .plan_checks import ConfigurationChecker
from .plan_postprocess import compact_plan_waypoints, smooth_plan_waypoints
from .plan_types import (_FAILURE_PRIORITY, _OK_STATUSES, Goal,
                         MotionContext, MotionPlan, MotionSegment,
                         PlanBudget, PlanBudgetExceeded, PlanStatus,
                         SegmentCheck)
from .trajectory import cartesian_poses, joint_distance, slerp


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
        # 阶段 2.02: 最近一次笛卡尔整链搜索的覆盖/剪枝统计 (06-10 §8.5 D).
        self.last_chain_search: dict = {}

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
                   thorough: bool = False,
                   lock: Optional[tuple[int, float, float]] = None,
                   extra_seeds: Optional[Sequence[Sequence[float]]] = None
                   ) -> list[list[float]]:
        """返回按关节连续性排序的候选解.

        ``thorough=True`` 只在自由关节段使用 (多 seed 找安全分支);
        法向接近段以上一解为 seed, 避免段内分支跳变.
        ``lock=(关节下标, 固定值, 容差)`` 通过收窄 simIK 关节区间求解,
        并对结果再做一次绝对偏差过滤; 无解返回空列表, 不跨分支回退.
        ``extra_seeds`` 只供整链 beam 搜索使用: 上一候选层的有限构型作为
        额外初值, 结果仍由调用方执行同一安全检查.
        """
        seeds = self._seeds(primary, thorough=thorough)
        for seed in extra_seeds or ():
            values = [float(v) for v in seed]
            if len(values) != 6:
                continue
            if any(joint_distance(values, known) < cfg.IK_DEDUPE_TOL * 0.5
                   for known in seeds):
                continue
            seeds.append(values)
            if len(seeds) >= cfg.IK_MAX_SEEDS:
                break
        kwargs = {}
        if lock is not None:
            joint, value, _accept_tol = lock
            # IK 区间半宽与验收容差分开: 求解器只允许 5 mrad 漂移,
            # 候选/执行验收允许 20 mrad 的数值误差.
            kwargs = {"lock_joint": int(joint), "lock_value": float(value),
                      "lock_tol": cfg.Q0_LOCK_IK_TOL}
        results = self.ik.solve_candidates(
            list(pose), seeds, max_candidates=cfg.IK_MAX_CANDIDATES,
            dedupe_tol=cfg.IK_DEDUPE_TOL, **kwargs)
        qs = [list(map(float, r.joints)) for r in results if r.success]
        if lock is not None:
            joint, value, tol = lock
            qs = [q for q in qs if abs(q[joint] - float(value))
                  <= float(tol) + 1e-9]
        reference = [float(v) for v in primary]

        def score(q):
            return (joint_distance(q, reference)
                    + cfg.IK_WRIST_PENALTY_WEIGHT * abs(_wrap_pi(q[5])))
        return sorted(qs, key=score)

    # ── 规划入口 ────────────────────────────────────────────

    def plan(self, start_q: Sequence[float], goals: Sequence[Goal],
             context: MotionContext,
             start_pose: Optional[Sequence[float]] = None,
             lock_fallback: Optional[bool] = None,
             direct_only: bool = False,
             wall_s: Optional[float] = None,
             max_samples: Optional[int] = None
             ) -> MotionPlan:
        """规划入口.

        ``direct_only=True`` 只使用 ``direct`` 策略, 禁止 HOME/抬高中转等
        关节路径变体; 06-15 的水平转运与法向下探使用该模式, 保证不会
        自动改走未验证的关节路径. ``wall_s``/``max_samples`` 为单次规划
        的预算覆盖, 只放宽时间/采样上限, 不减少任何检查.
        """
        started = time.monotonic()
        if wall_s is not None or max_samples is not None:
            budget = PlanBudget(
                wall_s=(cfg.PLAN_WALL_BUDGET_S if wall_s is None
                        else float(wall_s)),
                max_samples=(cfg.PLAN_MAX_TOTAL_SAMPLES
                             if max_samples is None else int(max_samples)))
        else:
            budget = self.budget or PlanBudget()
        self.checker.budget = budget
        anchor, margin = wrist_branch(start_q)
        if anchor is None or margin < cfg.WRIST_BRANCH_MARGIN:
            return MotionPlan(
                PlanStatus.WRIST_SINGULARITY,
                message=f"起始构型腕部裕量 {margin:.3f} < "
                        f"{cfg.WRIST_BRANCH_MARGIN:.3f} rad, 拒绝规划")
        context = context.with_flags(branch=anchor,
                                     branch_margin=cfg.WRIST_BRANCH_MARGIN)
        requested = [goal.name for goal in goals
                     if goal.lock_joint is not None]
        passes: list[tuple[str, list[Goal]]] = []
        if requested:
            passes.append(("lock", list(goals)))
            fallback = (cfg.Q0_LOCK_FALLBACK if lock_fallback is None
                        else bool(lock_fallback))
            if fallback:
                passes.append(("free", _without_locks(goals)))
        else:
            passes.append(("free", list(goals)))

        attempts: list[dict] = []
        best: Optional[MotionPlan] = None
        exhausted = False
        for policy, goals_variant in passes:
            for name, transit, strategy_goals in self._strategies(
                    goals_variant):
                if direct_only and name != "direct":
                    continue
                label = f"{policy}:{name}"
                try:
                    plan = self._attempt(
                        name, start_q, strategy_goals, context,
                        start_pose, transit, budget, policy)
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
                plan.strategy = label
                plan.branch_anchor = anchor
                plan.lock_policy = policy
                plan.locked_segments = requested if policy == "lock" else []
                plan.wall_s = time.monotonic() - started
                plan.samples = budget.samples
                plan.configs = budget.configs
                if plan.ok:
                    verified = None
                    compact = {}
                    smoothing = {}
                    try:
                        compact = compact_plan_waypoints(
                            plan, self.checker)
                        smoothing = smooth_plan_waypoints(
                            plan, self.checker)
                        self._finalize_plan_metrics(plan)
                        verified = self._verify_plan(plan)
                        if not verified.ok and smoothing.get("applied"):
                            _restore_waypoints(plan, smoothing)
                            smoothing["reverted"] = True
                            smoothing["reverted_reason"] = verified.message
                            self._finalize_plan_metrics(plan)
                            verified = self._verify_plan(plan)
                        if (not verified.ok and compact.get("removed")):
                            _restore_waypoints(plan, compact)
                            self._finalize_plan_metrics(plan)
                            verified = self._verify_plan(plan)
                            compact["reverted"] = True
                            compact["reverted_reason"] = verified.message
                        compact.pop("_original_waypoints", None)
                        compact.pop("_original_counts", None)
                        smoothing.pop("_original_waypoints", None)
                        smoothing.pop("_original_counts", None)
                        plan.compact = compact
                        plan.smoothing = smoothing
                    except PlanBudgetExceeded as exc:
                        exhausted = True
                        plan.status = PlanStatus.PLAN_BUDGET_EXCEEDED
                        plan.message = f"最终复核预算耗尽: {exc}"
                    except Exception as exc:
                        plan.status = PlanStatus.LOCAL_AVOIDANCE_FAILED
                        plan.message = (f"最终复核异常: "
                                        f"{type(exc).__name__}: {exc}")
                    if verified is not None and verified.ok:
                        plan.message = (
                            f"{len(plan.segments)} 段通过粗筛与最终复核"
                            f" (policy={policy})")
                        plan.attempts = attempts + [{
                            "strategy": label,
                            "status": plan.status.value,
                            "message": plan.message,
                        }]
                        if cfg.PLAN_TRACE_ENABLED:
                            try:
                                plan.diagnostics = \
                                    motion_diagnostics.build_plan_trace(
                                        plan, self.checker)
                            except Exception as exc:
                                plan.diagnostics = {
                                    "enabled": True, "points": 0,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                        return plan
                    if verified is not None:
                        plan.status = verified.status
                        plan.message = f"最终复核失败: {verified.message}"
                plan.attempts = attempts + [{
                    "strategy": label,
                    "status": plan.status.value,
                    "message": plan.message,
                }]
                if best is None or (_FAILURE_PRIORITY.get(plan.status, 0)
                                    > _FAILURE_PRIORITY.get(best.status, 0)):
                    best = plan
                attempts.append({
                    "strategy": label,
                    "status": plan.status.value,
                    "message": plan.message,
                })
                if exhausted or time.monotonic() - started > \
                        cfg.PLAN_WALL_BUDGET_S:
                    return _best_plan(best, attempts, started, budget)
            if exhausted:
                break
        return _best_plan(best, attempts, started, budget)

    def _verify_plan(self, plan: MotionPlan) -> SegmentCheck:
        """对完整计划按 2° 采样重新执行全部检查 (粗筛不能代替复核)."""
        last = len(plan.waypoints) - 1
        total = 0
        spans = plan.segment_spans()
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
            for start, end, segment in spans:
                if start <= i < end:
                    segment.verify_samples += check.samples
                    segment.check_status = check.status.value
                    if check.min_distance is not None:
                        if segment.min_distance is None or \
                                check.min_distance < segment.min_distance:
                            segment.min_distance = check.min_distance
                    break
            if not check.ok:
                return check
        return SegmentCheck(
            PlanStatus.OK, plan.waypoints[0], plan.waypoints[-1],
            samples=total, message="最终复核通过")

    def _finalize_plan_metrics(self, plan: MotionPlan) -> None:
        if not cfg.MOTION_METRICS_ENABLED:
            plan.metrics = {}
            return
        segments = []
        for start, end, segment in plan.segment_spans():
            samples = plan.waypoints[start:end + 1]
            tcp_pose = None
            if segment.target_pose is not None and samples:
                tcp_pose = self.checker.tcp_at(samples[-1])
            segment_data = motion_metrics.segment_metrics(
                samples, name=segment.name, cartesian=segment.cartesian,
                target_pose=segment.target_pose, tcp_pose=tcp_pose,
                lock_joint=segment.lock_joint,
                lock_value=segment.lock_value,
                lock_tol=segment.lock_tol)
            segment_data.update({
                "verify_samples": segment.verify_samples,
                "check_status": segment.check_status,
                "min_distance": segment.min_distance,
                "min_workcell_distance": self._min_workcell_distance(
                    samples),
            })
            segments.append(segment_data)
        plan.metrics = {
            "segments": segments,
            "totals": motion_metrics.totals(segments),
            "branch_anchor": plan.branch_anchor,
            "lock_policy": plan.lock_policy,
        }

    def _min_workcell_distance(self, samples: Sequence[Sequence[float]]
                               ) -> Optional[float]:
        """段内每个路径点的机器人-工作区精确最小距离 (m).

        与 ``check_segment`` 的阈值查询不同, 这里用 ``threshold=0`` 取精确
        距离, 只按路径点采样 (不是每个 2° 细分), 作为可比较的净空指标.
        查询异常返回 None, 不影响已经通过的碰撞检查.
        """
        best: Optional[float] = None
        if not hasattr(self.checker, "set_configuration") or \
                not hasattr(self.checker, "collisions"):
            return None
        for q in samples:
            if self.checker.set_configuration(q) is not None:
                continue
            try:
                result = self.checker.collisions.distance_margin(0.0)
            except Exception:
                continue
            if result.distance is None:
                continue
            if best is None or result.distance < best:
                best = float(result.distance)
        return None if best is None else round(best, 6)

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
                 budget: PlanBudget, policy: str = "free") -> MotionPlan:
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
        chain_searches: list[dict] = []

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
                                              q_path[-1],
                                              waypoint_count=1))
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
                                              chosen, waypoint_count=1,
                                              target_pose=list(payload)))
                current_pose = list(payload)

        def lock_for(goal: Goal, q_prev) -> Optional[tuple[int, float, float]]:
            if policy != "lock" or goal.lock_joint is None:
                return None
            return (int(goal.lock_joint), float(q_prev[goal.lock_joint]),
                    float(goal.lock_tol))

        def search(gi, q_prev, pose_prev, q_cur, ctx_cur, seg_cur):
            if gi >= len(goals):
                return q_cur, ctx_cur, seg_cur
            goal = goals[gi]
            is_last = goal is goals[-1]
            goal_ctx = context.with_flags(
                goal_pose=list(goal.pose),
                allow_target_contact=goal.allow_target_contact,
                allow_final_load_contact=goal.allow_final_load_contact)
            lock = lock_for(goal, q_prev)
            if goal.cartesian:
                ok, chain, status, message = self._cartesian_chain(
                    q_prev, pose_prev, goal, goal_ctx, lock=lock)
                chain_searches.append({
                    "goal": goal.name,
                    **dict(self.last_chain_search),
                })
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
                    goal.name, chain[0], chain[-1], cartesian=True,
                    samples=len(chain) - 1, waypoint_count=len(chain) - 1,
                    target_pose=list(goal.pose),
                    **(_lock_fields(lock))))
                end_pose = self.checker.tcp_at(chain[-1]) or goal.pose
                return search(gi + 1, chain[-1], end_pose, new_q, new_ctx,
                              new_seg)

            if goal.forced_q is not None:
                # 调用方已完成该构型的下游链复核; 仍走下方同一段检查/
                # 终点位姿/碰撞复核. forced_q 只用于无锁段.
                if lock is not None:
                    state["status"] = PlanStatus.LOCAL_AVOIDANCE_FAILED
                    state["message"] = (
                        f"{goal.name}: forced_q 与 lock_joint 不能同时使用")
                    return None
                candidates = [[float(v) for v in goal.forced_q]]
            else:
                candidates = self.solve_pose(goal.pose, q_prev,
                                             thorough=True, lock=lock)
                if lock is not None and not candidates:
                    state["status"] = PlanStatus.JOINT_LOCK_UNAVAILABLE
                    state["message"] = (
                        f"{goal.name}: 固定 q[{lock[0]}]={lock[1]:.4f} rad "
                        f"(±{lock[2]:.4f}) 无解")
                    return None
                candidates = self._order_candidates(
                    candidates[:cfg.PLAN_MAX_WAYPOINT_CANDIDATES],
                    q_prev, goals, gi, policy)
            for candidate in candidates:
                state["nodes"] += 1
                if state["nodes"] > cfg.PLAN_MAX_DFS_NODES:
                    state["status"] = PlanStatus.PLAN_BUDGET_EXCEEDED
                    state["message"] = "局部回溯节点超限"
                    return None
                check = self.checker.check_segment(
                    q_prev, candidate, goal_ctx,
                    allow_target_contact=goal.allow_target_contact,
                    final_contact=goal.final_contact and is_last,
                    screen=True)
                if not check.ok:
                    state["status"], state["message"] = \
                        check.status, check.message
                    continue
                pos_err, ang_err = self._pose_error(
                    candidate, goal.pose, strict=goal.strict_orientation)
                if pos_err > cfg.EXEC_TCP_POS_TOL or \
                        ang_err > cfg.EXEC_TCP_ANG_TOL:
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
                    state["status"], state["message"] = \
                        final.status, final.message
                    continue
                new_q = list(q_cur) + [[float(v) for v in candidate]]
                new_ctx = list(ctx_cur) + [goal_ctx]
                new_seg = list(seg_cur) + [
                    MotionSegment(goal.name, q_prev, candidate,
                                  waypoint_count=1,
                                  target_pose=list(goal.pose),
                                  **(_lock_fields(lock)))]
                candidate_pose = self.checker.tcp_at(candidate) or goal.pose
                result = search(gi + 1, candidate, candidate_pose, new_q,
                                new_ctx, new_seg)
                if result is not None:
                    return result
            return None

        result = search(0, q_path[-1], current_pose, q_path,
                        waypoint_contexts, segments)
        if result is None:
            return MotionPlan(state["status"], message=state["message"],
                              chain_search={"searches": chain_searches})
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
                          chain_search={"searches": chain_searches},
                          message=f"{len(final_segs)} 段通过粗筛, 待最终复核")

    def _order_candidates(self, candidates, q_prev, goals, gi, policy):
        """按“本段关节距离 + 下一自由目标一步前瞻”排序候选.

        纯 first-feasible 会选中第一个可行链; 若该链在下一段需要大幅
        基座/腕部转动, 会得到高 TV 路径. 这里只对候选做一次低成本 IK
        前瞻, 让下一段代价低的候选先被尝试, 不改变任何安全检查.
        """
        if len(candidates) <= 1 or gi + 1 >= len(goals) or \
                goals[gi + 1].cartesian:
            return list(candidates)
        next_goal = goals[gi + 1]
        scored = []
        for candidate in candidates:
            score = joint_distance(candidate, q_prev)
            lock = None
            if policy == "lock" and next_goal.lock_joint is not None:
                lock = (int(next_goal.lock_joint),
                        float(candidate[next_goal.lock_joint]),
                        float(cfg.Q0_LOCK_IK_TOL))
            try:
                lookahead = self.solve_pose(next_goal.pose, candidate,
                                            thorough=False, lock=lock)
            except Exception:
                lookahead = []
            if lookahead:
                score += cfg.PLAN_LOOKAHEAD_WEIGHT * min(
                    joint_distance(candidate, value) for value in lookahead)
            else:
                score += cfg.PLAN_LOOKAHEAD_FAIL_PENALTY
            scored.append((score, candidate))
        scored.sort(key=lambda item: item[0])
        return [candidate for _, candidate in scored]

    def _cartesian_chain(self, start_q, start_pose, goal: Goal,
                         context: MotionContext,
                         lock: Optional[tuple[int, float, float]] = None):
        """从 start 位姿沿直线到 goal; 返回 (ok, q_chain, status, message).

        ``CARTESIAN_CHAIN_SEARCH`` 选择逐点 first-feasible (greedy, 阶段
        2.01 默认) 或有界整链候选选择 (beam, 06-10 §8.3.3). 两条路径使用
        同一 IK、姿态/偏差判据和 ``check_segment`` 放行规则; beam 只改变
        候选组合方式, 不降低任何安全检查.
        """
        if cfg.CARTESIAN_CHAIN_SEARCH == "beam":
            return self._cartesian_chain_beam(start_q, start_pose, goal,
                                              context, lock=lock)
        return self._cartesian_chain_greedy(start_q, start_pose, goal,
                                            context, lock=lock)

    def _cartesian_chain_greedy(self, start_q, start_pose, goal: Goal,
                                context: MotionContext,
                                lock: Optional[tuple[int, float, float]] = None):
        """阶段 2.01 first-feasible 法向段 (保留为默认对照)."""
        poses = cartesian_poses(start_pose, goal.pose,
                                cfg.CARTESIAN_IK_STEP)
        stats = {
            "mode": "greedy",
            "goal": goal.name,
            "layers": len(poses) - 1,
            "candidates": [],
            "failure_layer": None,
            "status": None,
            "message": "",
        }
        chain = [[float(v) for v in start_q]]
        pose_prev = list(start_pose)
        last_status, last_message = PlanStatus.IK_FAILED, "IK 无解"
        for index, pose in enumerate(poses[1:], start=1):
            candidates = self.solve_pose(pose, chain[-1], thorough=False,
                                         lock=lock)
            chosen = None
            if lock is not None and not candidates:
                last_status = PlanStatus.JOINT_LOCK_UNAVAILABLE
                last_message = (f"固定 q[{lock[0]}]={lock[1]:.4f} rad "
                                f"(±{lock[2]:.4f}) 无解")
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
            stats["candidates"].append({
                "layer": index, "generated": len(candidates),
                "feasible": 1 if chosen is not None else 0,
            })
            if chosen is None:
                stats["failure_layer"] = index
                stats["status"] = last_status.value
                stats["message"] = last_message
                self.last_chain_search = stats
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
            stats["status"] = final.status.value
            stats["message"] = final.message
            self.last_chain_search = stats
            return (False, chain, final.status,
                    f"目标 {goal.name}: {final.message}")
        stats["status"] = PlanStatus.OK.value
        stats["message"] = f"{len(chain) - 1} 个法向点通过"
        self.last_chain_search = stats
        return (True, chain, PlanStatus.OK,
                f"{len(chain) - 1} 个法向点通过")

    def _cartesian_chain_beam(self, start_q, start_pose, goal: Goal,
                              context: MotionContext,
                              lock: Optional[tuple[int, float, float]] = None):
        """有界整链候选选择 (06-10 §8.3.3 / §8.5 D).

        每个笛卡尔采样层保留有限 IK 候选; 相邻层只在 ``check_segment``
        通过时连边; 用 beam 搜索在整段上选链, 而不是每层取第一个安全解.
        候选数/beam 宽度/节点数都有上限, 预算耗尽抛出
        :class:`PlanBudgetExceeded`, 不静默退化为未比较路径.
        """
        poses = cartesian_poses(start_pose, goal.pose,
                                cfg.CARTESIAN_IK_STEP)
        last_index = len(poses) - 1
        beam_width = max(1, int(cfg.CARTESIAN_BEAM_WIDTH))
        layer_limit = max(1, int(cfg.CARTESIAN_LAYER_CANDIDATES))
        nodes: list[dict] = [{
            "q": [float(v) for v in start_q],
            "chain": [[float(v) for v in start_q]],
            "cost": 0.0,
            "parts": {"tv_total": 0.0, "tv_q0": 0.0, "tv_q3": 0.0,
                      "b_all": 0.0, "a_all": 0.0},
        }]
        stats = {
            "mode": "beam",
            "goal": goal.name,
            "beam_width": beam_width,
            "layer_candidates": layer_limit,
            "layers": last_index,
            "expanded_nodes": 0,
            "edges_checked": 0,
            "edges_rejected": {},
            "layer_stats": [],
            "failure_layer": None,
            "status": None,
            "message": "",
        }
        last_status, last_message = PlanStatus.IK_FAILED, "IK 无解"

        def reject(status: PlanStatus) -> None:
            key = status.value
            stats["edges_rejected"][key] = \
                stats["edges_rejected"].get(key, 0) + 1

        for index, pose in enumerate(poses[1:], start=1):
            seeds = [node["q"] for node in sorted(
                nodes, key=lambda item: item["cost"])]
            candidates = self.solve_pose(
                pose, seeds[0], extra_seeds=seeds[1:], lock=lock)
            if not candidates:
                if lock is not None:
                    last_status = PlanStatus.JOINT_LOCK_UNAVAILABLE
                    last_message = (f"固定 q[{lock[0]}]={lock[1]:.4f} rad "
                                    f"(±{lock[2]:.4f}) 无解")
                stats["failure_layer"] = index
                stats["status"] = last_status.value
                stats["message"] = f"第 {index} 层无 IK 候选: {last_message}"
                stats["layer_stats"].append({
                    "layer": index, "generated": 0, "feasible": 0,
                    "kept": 0})
                self.last_chain_search = stats
                return (False, [list(chain) for chain in nodes[0]["chain"]],
                        last_status, stats["message"])
            ranked = []
            for candidate in candidates:
                pos_err, ang_err = self._pose_error(
                    candidate, pose,
                    strict=(goal.strict_orientation
                            and index == last_index))
                if pos_err > cfg.EXEC_TCP_POS_TOL or \
                        ang_err > cfg.EXEC_TCP_ANG_TOL:
                    last_status = PlanStatus.GOAL_CONFIG_INVALID
                    last_message = (f"IK 未到达法向点: pos={pos_err:.4f} "
                                    f"ang={ang_err:.4f}")
                    reject(PlanStatus.GOAL_CONFIG_INVALID)
                    continue
                nearest = min(joint_distance(node["q"], candidate)
                              for node in nodes)
                ranked.append((nearest, pos_err, ang_err, candidate))
            ranked.sort(key=lambda item: (item[0], item[1], item[2]))
            ranked = ranked[:layer_limit]
            new_nodes: list[dict] = []
            for _nearest, _pos, _ang, candidate in ranked:
                best_parent = None
                best_increment = None
                best_total = None
                for node in sorted(nodes, key=lambda item: item["cost"]):
                    deviation = self._cartesian_deviation(
                        node["q"], candidate, poses[index - 1], pose)
                    if not deviation["ok"]:
                        last_status = PlanStatus.GOAL_CONFIG_INVALID
                        last_message = (
                            f"法向插值偏差 "
                            f"lateral={deviation['lateral']:.4f} m "
                            f"ang={deviation['angle']:.4f} rad")
                        reject(PlanStatus.GOAL_CONFIG_INVALID)
                        continue
                    check = self.checker.check_segment(
                        node["q"], candidate, context,
                        allow_target_contact=goal.allow_target_contact,
                        final_contact=(goal.final_contact
                                       and index == last_index),
                        cartesian=False, screen=True,
                        allow_start_load_contact=(
                            context.allow_start_load_contact and index == 1),
                        allow_end_load_contact=(
                            goal.allow_final_load_contact
                            and index == last_index))
                    stats["edges_checked"] += 1
                    if not check.ok:
                        last_status, last_message = check.status, check.message
                        reject(check.status)
                        continue
                    increment = motion_metrics.chain_cost_increment(
                        node["chain"], candidate)
                    total = node["cost"] + increment["cost"]
                    if best_total is None or total < best_total:
                        best_parent = node
                        best_increment = increment
                        best_total = total
                if best_parent is None:
                    continue
                parts = {
                    key: best_parent["parts"][key] + best_increment[key]
                    for key in ("tv_total", "tv_q0", "tv_q3", "b_all",
                                "a_all")
                }
                new_nodes.append({
                    "q": [float(v) for v in candidate],
                    "chain": best_parent["chain"]
                             + [[float(v) for v in candidate]],
                    "cost": best_total,
                    "parts": parts,
                })
            if not new_nodes:
                stats["failure_layer"] = index
                stats["status"] = last_status.value
                stats["message"] = f"第 {index} 层无可行候选: {last_message}"
                stats["layer_stats"].append({
                    "layer": index, "generated": len(ranked),
                    "feasible": 0, "kept": 0})
                self.last_chain_search = stats
                return (False, [list(chain) for chain in nodes[0]["chain"]],
                        last_status, stats["message"])
            deduped: dict[tuple, dict] = {}
            for node in sorted(new_nodes, key=lambda item: item["cost"]):
                key = tuple(round(v / cfg.IK_DEDUPE_TOL)
                            for v in node["q"])
                if key not in deduped:
                    deduped[key] = node
            kept = sorted(deduped.values(), key=lambda item: item["cost"])
            kept = kept[:beam_width]
            stats["layer_stats"].append({
                "layer": index, "generated": len(ranked),
                "feasible": len(new_nodes), "kept": len(kept),
            })
            stats["expanded_nodes"] += len(kept)
            if stats["expanded_nodes"] > cfg.CARTESIAN_CHAIN_MAX_NODES:
                raise PlanBudgetExceeded(
                    f"整链搜索节点 {stats['expanded_nodes']} > "
                    f"{cfg.CARTESIAN_CHAIN_MAX_NODES}")
            nodes = kept

        alternatives = []
        for node in sorted(nodes, key=lambda item: item["cost"]):
            alternatives.append({
                "cost": round(float(node["cost"]), 6),
                "parts": {key: round(float(value), 6)
                          for key, value in node["parts"].items()},
                "q_end": [round(float(v), 6) for v in node["q"]],
            })
            final = self.checker.check(
                node["q"], context,
                allow_target_contact=goal.allow_target_contact,
                final_contact=goal.final_contact,
                allow_load_contact=goal.allow_final_load_contact)
            if not final.ok:
                last_status, last_message = final.status, final.message
                continue
            pos_err, ang_err = self._pose_error(
                node["q"], goal.pose, strict=goal.strict_orientation)
            if pos_err > cfg.EXEC_TCP_POS_TOL or \
                    ang_err > cfg.EXEC_TCP_ANG_TOL:
                last_status = PlanStatus.GOAL_CONFIG_INVALID
                last_message = (f"最终 IK 未到达目标: pos={pos_err:.4f} "
                                f"ang={ang_err:.4f}")
                continue
            stats["status"] = PlanStatus.OK.value
            stats["message"] = (f"{len(node['chain']) - 1} 个法向点通过 "
                                f"(beam, cost={node['cost']:.4f})")
            stats["final_cost"] = round(float(node["cost"]), 6)
            stats["final_parts"] = {key: round(float(value), 6)
                                    for key, value in node["parts"].items()}
            stats["alternatives"] = alternatives[:5]
            self.last_chain_search = stats
            return (True, node["chain"], PlanStatus.OK, stats["message"])
        stats["status"] = last_status.value
        stats["message"] = f"目标 {goal.name}: {last_message}"
        stats["alternatives"] = alternatives[:5]
        self.last_chain_search = stats
        return (False, [list(chain) for chain in nodes[0]["chain"]],
                last_status, stats["message"])

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
    # forced_q 绑定原目标位姿; 旋转位姿后该构型的下游复核失效, 不得变体
    if any(goal.forced_q is not None for goal in goals):
        return list(goals)
    out = []
    for goal in goals:
        pose = list(goal.pose)
        pose[3:7] = rotate_about_axis(pose[3:7], (0.0, 0.0, 1.0), yaw)
        out.append(Goal(goal.name, pose, goal.cartesian,
                        goal.allow_target_contact, goal.final_contact,
                        goal.strict_orientation,
                        goal.allow_final_load_contact,
                        goal.lock_joint, goal.lock_tol, goal.forced_q))
    return out


def _without_locks(goals: Sequence[Goal]) -> list[Goal]:
    """同一目标/姿态/接触策略, 仅去掉段内固定关节."""
    return [Goal(goal.name, list(goal.pose), goal.cartesian,
                 goal.allow_target_contact, goal.final_contact,
                 goal.strict_orientation, goal.allow_final_load_contact,
                 None, goal.lock_tol, goal.forced_q) for goal in goals]


def _lock_fields(lock: Optional[tuple[int, float, float]]) -> dict:
    if lock is None:
        return {"lock_joint": None, "lock_value": None, "lock_tol": None}
    return {"lock_joint": int(lock[0]), "lock_value": float(lock[1]),
            "lock_tol": float(lock[2])}


def _best_plan(best: Optional[MotionPlan], attempts: list[dict],
               started: float, budget: PlanBudget) -> MotionPlan:
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


def _restore_waypoints(plan: MotionPlan, compact: dict) -> None:
    plan.waypoints = [list(q) for q in compact["_original_waypoints"]]
    for segment, count in zip(plan.segments,
                              compact["_original_counts"]):
        segment.waypoint_count = count
    compact.pop("_original_waypoints", None)
    compact.pop("_original_counts", None)

def _wrap_pi(angle: float) -> float:
    value = math.remainder(float(angle), 2.0 * math.pi)
    if value <= -math.pi:
        value += 2.0 * math.pi
    return value
