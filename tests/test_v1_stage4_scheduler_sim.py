"""阶段 4 WP4: `full_flow` 实机验收 (需要 CoppeliaSim 4.10 + ZMQ 23000).

    FLEXIBLE_FACTORY_SIM=1 python -m pytest -q \
        tests/test_v1_stage4_scheduler_sim.py -s

未启用时 skip, 报告中记"未执行", 不得按通过报告. 本用例只运行用户入口
`full_flow`, 不新增 scenario; 逐 tick 的控制器轨迹/规划详情在写证据前剔除,
保留验收字段、11 条物理工序决策、12 个工序实例、任务/航线/事件与指标.
"""

from __future__ import annotations

from flexible_factory.application.coordinator import Stage4Coordinator
from flexible_factory.domain.scheduling import SCHEDULER_POLICY_VERSION
from stage2_harness import drive, open_context, require_sim, write_evidence

HEAVY_SUMMARY_KEYS = ("events", "motion", "plan")


def _compact_history(rows: list[dict]) -> list[dict]:
    compact = []
    for row in rows:
        summary = dict(row.get("summary") or {})
        for key in HEAVY_SUMMARY_KEYS:
            summary.pop(key, None)
        compact.append({**row, "summary": summary})
    return compact


def _compact_tasks(queue: dict) -> dict:
    tasks = []
    for task in queue.get("tasks", []):
        row = dict(task)
        row.pop("evidence", None)
        tasks.append(row)
    return {**queue, "tasks": tasks}


def _evidence(coordinator: Stage4Coordinator, result: dict) -> dict:
    summary = coordinator.summary()
    process = summary.get("process_model") or {}
    recipe = (process.get("catalog") or {}).get("recipes") or []
    return {
        "source_report": "tests/test_v1_stage4_scheduler_sim.py",
        "note": ("full_flow 实机验收; 已剔除 controller_history 的逐 tick "
                 "motion/plan/events 与 task.evidence; 正式结论以 "
                 "acceptance/scheduler/events 为准"),
        "scenario": coordinator.scenario,
        "fault": coordinator.fault,
        "drive": result,
        "all_ok": bool((summary.get("acceptance") or {}).get("ok")),
        "acceptance": summary.get("acceptance"),
        "process_model": {
            "version": process.get("version"),
            "recipe_id": process.get("recipe_id"),
            "recipe_version": process.get("recipe_version"),
            "validation": process.get("validation"),
        },
        "recipe": recipe[0] if recipe else {},
        "operations": summary.get("operations"),
        "tasks": _compact_tasks(summary.get("tasks") or {}),
        "task_rejections": (summary.get("tasks") or {}).get("rejections", []),
        "routes": summary.get("routes"),
        "dispatch_decisions": summary.get("dispatch_decisions"),
        "scheduler": summary.get("scheduler"),
        "materials": summary.get("materials"),
        "events": summary.get("events"),
        "metrics": summary.get("metrics"),
        "controller_history": _compact_history(
            summary.get("controller_history") or []),
    }


def test_full_flow_uses_scheduler_decisions_and_matches_baseline():
    require_sim()
    with open_context() as ctx:
        coordinator = Stage4Coordinator(
            ctx.sim, ctx.scene, ctx.comp, scenario="full_flow",
            logger=ctx.logger)
        coordinator.start(coordinator._sim_time())
        # full_flow 约 338 s 仿真时间; 本机步进/规划墙钟可能 20–40 min.
        result = drive(coordinator, max_wall_s=3600.0)
        acceptance = coordinator.acceptance()
        payload = _evidence(coordinator, result)
        path = write_evidence("full_flow", payload, prefix="v1_stage4_")
        print(f"[stage4] full_flow 证据: {path}")

        assert result["finished"], result
        assert not coordinator.frozen, coordinator.stop_reason
        assert acceptance["ok"], acceptance
        assert acceptance["graph_complete"]
        assert acceptance["scheduler_ok"], acceptance["scheduler_detail"]
        assert acceptance["tasks_done"]
        assert acceptance["final_count"] == 1
        assert acceptance["final_parents"] == [[1, 3]]
        assert acceptance["unpaired_semi_ce"] == 1
        assert acceptance["loaded_routes"] == 2
        assert acceptance["route_ok"]
        assert acceptance["resources_released"]
        assert not coordinator.queue.rejections

        decisions = coordinator.schedule_decisions
        assert len(decisions) == 11
        assert all(row["policy_version"] == SCHEDULER_POLICY_VERSION
                   for row in decisions)
        assert all(row["not_modeled"] == [
            "device_health", "tool_payload", "pose_envelope"]
            for row in decisions)
        assert all(row["execution_id"] for row in decisions)
        assert all(
            row["selected_device_id"]
            in [candidate["device_id"] for candidate in row["candidates"]
                if candidate["accepted"]]
            for row in decisions)
        # 默认 full_flow 仍使用 recipe 声明的固定候选, 不宣称跨设备替代.
        for row in decisions:
            accepted = [candidate["device_id"] for candidate
                        in row["candidates"] if candidate["accepted"]]
            assert row["selected_device_id"] == accepted[0]
        assert all(owner is None
                   for owner in coordinator.resources.owners.values())
        assert all(instance.selected_device_id
                   for instance in coordinator.op_runtime.graph.instances()
                   if instance.template_id != "kit_join")
        assert coordinator.queue.all_done()
