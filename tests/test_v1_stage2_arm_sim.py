"""阶段 2 步骤 3 focused: arm_A 在真实控制器/API 路径上完成一次取放.

需要本机 CoppeliaSim 4.10 与已加载 v1 工件:

    FLEXIBLE_FACTORY_SIM=1 python -m pytest -q \
        tests/test_v1_stage2_arm_sim.py -s

未启用时 skip, 报告中记为"未执行"; 只跑代表臂 `arm_A`, 其余实例由
`test_v1_device_binding.py` 的静态绑定核对覆盖。

证据写入 `docs/implementation/runs/` 时去掉逐 tick 的
`controller.summary()["motion"/"plan"/"events"]` 与 `task.evidence`
(单次运行可达数 MB); 验收字段与判据不受影响。
"""

from __future__ import annotations

import math

from flexible_factory.domain.tasks import TaskStatus
from flexible_factory.simulation import config_motion as cfg
from flexible_factory.simulation.motion.contact import quat_angle_between
from flexible_factory.simulation.motion.path_library import PathLibrary
from flexible_factory.simulation.motion.path_reuse import PathReusePolicy
from stage2_harness import drive, open_context, require_sim, write_evidence

HEAVY_SUMMARY_KEYS = ("events", "motion", "plan")


def _compact_history(rows: list[dict]) -> list[dict]:
    """保留状态/证据标量, 去掉逐 tick 轨迹与事件日志."""
    compact = []
    for row in rows:
        summary = dict(row.get("summary") or {})
        for key in HEAVY_SUMMARY_KEYS:
            summary.pop(key, None)
        compact.append({**row, "summary": summary})
    return compact


def _compact_task(task) -> dict:
    """任务字段 + 只读投影; `evidence` 是控制器快照副本, 不重复入库."""
    data = task.as_dict()
    data.pop("evidence", None)
    return data


def test_arm_a_direct_pick_place_reaches_handoff():
    require_sim()
    with open_context() as ctx:
        coordinator = ctx.new_coordinator()
        task = ctx.inject_source(coordinator, source_key="s1a",
                                 operation_id="housing_direct_A", uid=1)

        def _stop() -> bool:
            stored = coordinator.queue.get(task.task_id)
            return (stored is None or stored.status.terminal
                    or coordinator.frozen)

        result = drive(coordinator, should_stop=_stop, max_wall_s=600.0)
        stored = coordinator.queue.get(task.task_id)
        flow = coordinator.flow.by_uid(1)
        history = _compact_history(
            [row for row in coordinator.controller_history
             if row.get("task_id") == task.task_id])
        payload = {
            "case": "stage2_arm_a_direct_pick_place",
            "operation": "housing_direct_A",
            "device": "arm_A",
            "drive": result,
            "task": None if stored is None else _compact_task(stored),
            "flow": None if flow is None else flow.as_dict(),
            "controller_history": history,
            "acceptance": coordinator.acceptance(),
            "evidence_note": ("已去掉逐 tick 的 summary.motion/plan/events 与 "
                              "task.evidence; 完整输出见 runs/ (不入库)"),
        }
        path = write_evidence("arm", payload)
        print(f"[stage2] arm 证据: {path}")

        assert stored is not None
        assert stored.status is TaskStatus.DONE, stored.failure_reason
        assert flow is not None
        assert flow.zone == "handoff_ac"
        assert flow.item_type.value == "SEMI_AC"
        assert history and history[-1]["execution_id"].startswith("arm_A#")
        assert not coordinator.frozen


# ═══════════════════════════════════════════════════════════════
# P1 focused: 同一固定任务 PLAN_ONLY → CACHE_ONLY
# ═══════════════════════════════════════════════════════════════

def _run_unit_task(ctx, policy):
    """跑一次 A 臂 conveyor→handoff 任务; 返回协调器/任务/历史."""
    coordinator = ctx.new_coordinator(path_reuse=policy)
    task = ctx.inject_source(coordinator, source_key="s1a",
                             operation_id="housing_direct_A", uid=1)

    def _stop() -> bool:
        stored = coordinator.queue.get(task.task_id)
        return (stored is None or stored.status.terminal
                or coordinator.frozen)

    result = drive(coordinator, should_stop=_stop, max_wall_s=600.0)
    stored = coordinator.queue.get(task.task_id)
    history = [row for row in coordinator.controller_history
               if row.get("task_id") == task.task_id]
    return coordinator, stored, result, history


def _placing_motion(history):
    for row in history:
        summary = row.get("summary") or {}
        for motion in summary.get("motion") or []:
            if motion.get("state") == "PLACING":
                return motion
    return None


def _target_error(motion):
    plan = motion.get("plan") or {}
    goal = plan.get("goal_pose")
    tcp = motion.get("tcp_pose")
    if goal is None or tcp is None:
        return float("inf"), float("inf")
    return (math.dist(tcp[:3], goal[:3]),
            quat_angle_between(tcp[3:7], goal[3:7]))


def _motion_payload(motion):
    if motion is None:
        return None
    execution = dict(motion.get("execution") or {})
    execution.pop("trace", None)
    return {
        "status": motion.get("status"),
        "plan_source": motion.get("plan_source"),
        "replay": motion.get("replay"),
        "tcp_pose": motion.get("tcp_pose"),
        "plan": motion.get("plan"),
        "execution": execution,
    }


def _safety_engaged(history):
    summary = (history[-1].get("summary") if history else {}) or {}
    return bool((summary.get("safety") or {}).get("engaged"))


def test_arm_a_placing_path_reuse_plan_only_then_cache_only(tmp_path):
    """固定任务先在线规划并导出, 再重载场景用 CACHE_ONLY 回放同一 PLACING 段."""
    require_sim()
    library_root = tmp_path / "path_library"
    payload = {"case": "stage2_arm_a_placing_path_reuse"}

    with open_context(log_path=str(tmp_path / "reuse_run1.log")) as ctx1:
        scene_id = str(ctx1.manager.session.artifact_id or "")
        assert scene_id, "工件缺少 artifact_id, 无法形成路径键"
        policy1 = PathReusePolicy(
            PathLibrary(library_root), "PLAN_ONLY",
            scene_artifact_id=scene_id, export=True,
            enabled_units=("A",))
        coordinator1, stored1, drive1, history1 = _run_unit_task(
            ctx1, policy1)
        placing1 = _placing_motion(history1)
        pos1, ang1 = _target_error(placing1 or {})
        payload["run1"] = {
            "scene_artifact_id": scene_id,
            "drive": drive1,
            "task_status": (None if stored1 is None
                            else stored1.status.value),
            "placing": _motion_payload(placing1),
            "target_error": {"pos": pos1, "ang": ang1},
            "policy": policy1.stats(),
            "safety_engaged": _safety_engaged(history1),
        }
        path1 = write_evidence("path_reuse_run1", payload,
                               prefix="v1_motion_path_reuse_")
        print(f"[path-reuse] run1 evidence: {path1}")

        assert stored1 is not None and stored1.status is TaskStatus.DONE
        assert not coordinator1.frozen
        assert placing1 is not None and placing1.get("status") == "DONE"
        assert placing1.get("plan_source") == "online"
        assert (placing1.get("replay") or {}).get("export", {}).get(
            "saved") is True
        assert pos1 <= cfg.EXEC_TCP_POS_TOL, (pos1, ang1)
        assert ang1 <= cfg.EXEC_TCP_ANG_TOL, (pos1, ang1)
        assert (placing1.get("execution") or {}).get(
            "totals", {}).get("steps", 0) > 0
        assert not _safety_engaged(history1)

    with open_context(log_path=str(tmp_path / "reuse_run2.log")) as ctx2:
        scene_id2 = str(ctx2.manager.session.artifact_id or "")
        assert scene_id2 == scene_id
        policy2 = PathReusePolicy(
            PathLibrary(library_root), "CACHE_ONLY",
            scene_artifact_id=scene_id, enabled_units=("A",))
        coordinator2, stored2, drive2, history2 = _run_unit_task(
            ctx2, policy2)
        placing2 = _placing_motion(history2)
        pos2, ang2 = _target_error(placing2 or {})
        payload["run2"] = {
            "scene_artifact_id": scene_id2,
            "drive": drive2,
            "task_status": (None if stored2 is None
                            else stored2.status.value),
            "placing": _motion_payload(placing2),
            "target_error": {"pos": pos2, "ang": ang2},
            "policy": policy2.stats(),
            "safety_engaged": _safety_engaged(history2),
        }
        if placing1 is not None and placing2 is not None:
            payload["comparison"] = {
                "tcp_distance": math.dist(placing1["tcp_pose"][:3],
                                          placing2["tcp_pose"][:3]),
                "pos_delta": abs(pos2 - pos1),
                "ang_delta": abs(ang2 - ang1),
            }
        path2 = write_evidence("path_reuse_run2", payload,
                               prefix="v1_motion_path_reuse_")
        print(f"[path-reuse] run2 evidence: {path2}")

        assert stored2 is not None and stored2.status is TaskStatus.DONE
        assert not coordinator2.frozen
        assert placing2 is not None and placing2.get("status") == "DONE"
        assert placing2.get("plan_source") == "cache"
        assert policy2.planner_calls == 0
        assert policy2.lookup_count >= 1
        assert policy2.replay_count == 1
        assert (placing2.get("replay") or {}).get("phases") == [
            "PR_LOOKUP", "PR_REPLAY", "PR_DONE"]
        assert pos2 <= cfg.EXEC_TCP_POS_TOL, (pos2, ang2)
        assert ang2 <= cfg.EXEC_TCP_ANG_TOL, (pos2, ang2)
        assert (placing2.get("execution") or {}).get(
            "totals", {}).get("steps", 0) > 0
        assert not _safety_engaged(history2)
        assert math.dist(placing1["tcp_pose"][:3],
                         placing2["tcp_pose"][:3]) <= 0.005
