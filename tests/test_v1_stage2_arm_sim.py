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

from flexible_factory.domain.tasks import TaskStatus
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
