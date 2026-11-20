"""path_reuse.py — 窄路径复用策略 (PLAN_ONLY / CACHE_ONLY / CACHE_THEN_PLAN).

第一版只包装一个固定动作段的规划调用:

  - ``PLAN_ONLY``: 始终调用现有 ``LocalPlanner``, 可选导出成功路径;
  - ``CACHE_ONLY``: 只回放精确匹配路径, 未命中返回 ``PATH_REPLAY_MISS``;
  - ``CACHE_THEN_PLAN``: 先回放, 未命中时调用现有规划器一次, 可选导出.

回放只跳过重复 IK/候选搜索, 不取消执行期联锁。命中后用**当前**动作请求
重建 ``MotionContext`` 并交给原 ``JointStreamExecutor``; 缓存不保存
CoppeliaSim 句柄、旧 context 或旧运行期状态。本模块不修改
``LocalPlanner.plan()`` 的搜索行为, 也不拥有 ``sim.step()``。
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Sequence

from .. import config_motion as cfg
from .branch import wrist_branch
from .path_library import (PATH_RECORD_VERSION, PATH_REUSE_MODES, PR_DONE,
                           PR_LOOKUP, PR_PLAN_FALLBACK, PR_REPLAY,
                           PathKey, PathLibrary, PathRecord,
                           PathRecordError, goals_digest,
                           motion_config_version, record_from_plan,
                           start_digest)
from .plan_types import (Goal, MotionContext, MotionPlan, MotionSegment,
                         PlanStatus)
from .trajectory import joint_distance

PATH_REUSE_VERSION = "v1_path_reuse_20261120"


class PathReusePolicy:
    """在规划器调用点外层执行的路径复用策略.

    ``enabled_phases``/``enabled_units`` 只用于把首版限定在试点动作:
    默认只对 A 臂 ``PLACING`` 生效; 控制器仍保留全部原有规划路径,
    未启用的状态不会调用本策略。
    """

    def __init__(self, library: PathLibrary, mode: str = "PLAN_ONLY", *,
                 scene_artifact_id: str, robot: str = "UR5",
                 export: bool = False,
                 enabled_phases: Sequence[str] = ("PLACING",),
                 enabled_units: Optional[Sequence[str]] = ("A",),
                 clock: Callable[[], float] = time.monotonic) -> None:
        if mode not in PATH_REUSE_MODES:
            raise ValueError(
                f"未知路径复用模式 {mode!r}; 可选 {PATH_REUSE_MODES}")
        self.library = library
        self.mode = str(mode)
        self.scene_artifact_id = str(scene_artifact_id or "").strip()
        if not self.scene_artifact_id:
            raise ValueError("scene_artifact_id 不能为空; 路径键必须含场景工件 ID")
        self.robot = str(robot)
        self.export = bool(export)
        self.enabled_phases = tuple(str(value) for value in enabled_phases)
        self.enabled_units = (None if enabled_units is None else
                              tuple(str(value) for value in enabled_units))
        self._clock = clock
        self.lookup_count = 0
        self.replay_count = 0
        self.fallback_count = 0
        self.planner_calls = 0

    # ── 适用范围 ────────────────────────────────────────────

    def applies(self, unit_id: str, state_name: str) -> bool:
        """只对试点工位/动作状态返回 True."""
        if self.enabled_units is not None and \
                str(unit_id) not in self.enabled_units:
            return False
        return str(state_name) in self.enabled_phases

    def stats(self) -> dict:
        return {
            "version": PATH_REUSE_VERSION,
            "mode": self.mode,
            "scene": self.scene_artifact_id,
            "lookup": self.lookup_count,
            "replay": self.replay_count,
            "fallback": self.fallback_count,
            "planner_calls": self.planner_calls,
            "export": self.export,
            "last_error": self.library.last_error,
        }

    # ── 规划入口 ────────────────────────────────────────────

    def plan(self, planner, start_q: Sequence[float],
             goals: Sequence[Goal], context: MotionContext, *,
             phase: str, unit_id: str, tool: str, payload: str,
             start_pose: Optional[Sequence[float]] = None,
             **plan_kwargs) -> MotionPlan:
        """按模式回放或调用现有规划器; ``goals`` 首版只允许单目标."""
        started = self._clock()
        key = PathKey(
            scene=self.scene_artifact_id,
            phase=str(phase),
            unit=str(unit_id),
            robot=self.robot,
            tool=str(tool),
            payload=str(payload),
            start_digest=start_digest(start_q),
            targets_digest=goals_digest(goals),
            motion_version=motion_config_version(),
        )
        phases: list[str] = []
        miss_reason = ""
        if self.mode != "PLAN_ONLY":
            phases.append(PR_LOOKUP)
            self.lookup_count += 1
            record = self.library.get_exact(key)
            if record is None:
                miss_reason = self.library.last_error or "未找到匹配路径记录"
            else:
                ready, miss_reason = self._replay_ready(
                    record, start_q, context, goals)
                if ready:
                    plan = self._plan_from_record(record, goals, context)
                    elapsed = max(0.0, self._clock() - started)
                    plan.wall_s = elapsed
                    plan.timings = _replay_timings()
                    plan.replay = {
                        "version": PATH_REUSE_VERSION,
                        "mode": self.mode,
                        "record_id": key.path_id(),
                        "record_path": str(self.library.path_for(key)),
                        "original_strategy": record.strategy,
                        "original_status": record.status,
                        "lookup_wall_s": round(elapsed, 6),
                        "phases": phases + [PR_REPLAY],
                    }
                    self.replay_count += 1
                    return plan
            if self.mode == "CACHE_ONLY":
                elapsed = max(0.0, self._clock() - started)
                return MotionPlan(
                    PlanStatus.PATH_REPLAY_MISS,
                    message=f"路径缓存未命中: {miss_reason}",
                    wall_s=elapsed,
                    timings=_replay_timings(),
                    plan_source="cache",
                    replay={
                        "version": PATH_REUSE_VERSION,
                        "mode": self.mode,
                        "record_id": key.path_id(),
                        "miss_reason": miss_reason,
                        "phases": phases,
                    })
            phases.append(PR_PLAN_FALLBACK)
            self.fallback_count += 1

        # 在线规划: 仍只调用一次现有 LocalPlanner, 保持原搜索/检查行为.
        self.planner_calls += 1
        plan = planner.plan(start_q, goals, context,
                            start_pose=start_pose, **plan_kwargs)
        plan.plan_source = "online"
        export_info = {"enabled": self.export, "saved": False}
        if self.export and plan.ok and len(goals) == 1:
            try:
                record = record_from_plan(plan, key)
                if record is not None:
                    path = self.library.put(record)
                    export_info.update(saved=True, record_id=key.path_id(),
                                       path=str(path))
            except (OSError, PathRecordError) as exc:
                export_info["error"] = f"{type(exc).__name__}: {exc}"
        plan.replay = {
            "version": PATH_REUSE_VERSION,
            "mode": self.mode,
            "record_id": key.path_id(),
            "fallback_reason": miss_reason if PR_PLAN_FALLBACK in phases
            else "",
            "phases": phases,
            "export": export_info,
        }
        return plan

    # ── 回放条件 ────────────────────────────────────────────

    def _replay_ready(self, record: PathRecord,
                      start_q: Sequence[float],
                      context: MotionContext,
                      goals: Sequence[Goal]) -> tuple[bool, str]:
        if len(goals) != 1:
            return False, "首版只回放单目标动作"
        if record.format_version != PATH_RECORD_VERSION:
            return False, "记录版本不匹配"
        if len(start_q) != len(record.start_q):
            return False, "起始关节维度与记录不一致"
        deviation = joint_distance(start_q, record.start_q)
        if deviation > cfg.EXEC_JOINT_TOL:
            return False, (f"起始关节偏差 {deviation:.4f} rad > "
                           f"{cfg.EXEC_JOINT_TOL:.4f} rad")
        anchor, margin = wrist_branch(start_q)
        if margin < cfg.WRIST_BRANCH_MARGIN:
            return False, (f"起始构型腕部裕量 {margin:.3f} rad < "
                           f"{cfg.WRIST_BRANCH_MARGIN:.3f} rad")
        if record.branch_anchor is not None and anchor != record.branch_anchor:
            return False, (f"起始腕部分支 {anchor} != 记录 "
                           f"{record.branch_anchor}")
        if context.branch is not None and \
                record.branch_anchor is not None and \
                context.branch != record.branch_anchor:
            return False, (f"上下文腕部分支 {context.branch} != 记录 "
                           f"{record.branch_anchor}")
        return True, ""

    def _plan_from_record(self, record: PathRecord,
                          goals: Sequence[Goal],
                          context: MotionContext) -> MotionPlan:
        """用当前实时 context 和目标重建可执行计划."""
        waypoints = [[float(v) for v in q] for q in record.waypoints]
        segments: list[MotionSegment] = []
        index = 0
        for row in record.segments:
            count = int(row["waypoint_count"])
            q_start = list(waypoints[index])
            index += count
            segments.append(MotionSegment(
                name=str(row["name"]),
                q_start=q_start,
                q_end=list(waypoints[index]),
                cartesian=bool(row.get("cartesian", False)),
                samples=0,
                waypoint_count=count,
                target_pose=(None if row.get("target_pose") is None else
                             [float(v) for v in row["target_pose"]]),
                lock_joint=row.get("lock_joint"),
                lock_value=row.get("lock_value"),
                lock_tol=row.get("lock_tol"),
            ))
        goal = goals[-1]
        goal_context = context.with_flags(
            goal_pose=list(goal.pose),
            allow_target_contact=bool(goal.allow_target_contact),
            allow_final_load_contact=bool(goal.allow_final_load_contact))
        contexts = [context] + [goal_context] * (len(waypoints) - 1)
        return MotionPlan(
            PlanStatus.OK,
            waypoints=waypoints,
            segments=segments,
            contexts=contexts,
            message=(f"路径回放: 记录 {record.key.path_id()} "
                     f"(原策略 {record.strategy})"),
            goal_pose=list(goal.pose),
            final_contact=bool(goal.final_contact),
            allow_final_load_contact=bool(goal.allow_final_load_contact),
            strict_orientation=bool(goal.strict_orientation),
            strategy="replay",
            branch_anchor=record.branch_anchor,
            lock_policy=record.lock_policy,
            locked_segments=list(record.locked_segments),
            metrics={
                "replay": {
                    "record_id": record.key.path_id(),
                    "original_strategy": record.strategy,
                    "points": len(waypoints),
                },
                "totals": {"points": len(waypoints)},
            },
            plan_source="cache",
        )


def mark_replay_done(plan: MotionPlan) -> None:
    """执行完成时补 ``PR_DONE``; 不改变计划路径或执行指标."""
    if not plan.replay:
        return
    phases = list(plan.replay.get("phases") or [])
    if PR_DONE not in phases:
        phases.append(PR_DONE)
    plan.replay["phases"] = phases


def _replay_timings() -> dict:
    """回放不执行搜索/复核/后处理; 三段均为 0, 查找耗时在 replay 中."""
    return {
        "search_wall_s": 0.0,
        "verify_wall_s": 0.0,
        "postprocess_wall_s": 0.0,
    }


__all__ = ["PATH_REUSE_VERSION", "PathReusePolicy", "mark_replay_done"]