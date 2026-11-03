"""阶段 2 步骤 4 focused: AGV 在真实轮速/API 路径上走一条代表路线.

覆盖同一路线的两种生命周期分支:
  1. 空载 reposition `A → B`;
  2. 载货 `B → A` (载台放置一件 SEMI_AB)。

需要本机 CoppeliaSim 4.10 与已加载 v1 工件:
    FLEXIBLE_FACTORY_SIM=1 python -m pytest -q \n        tests/test_v1_stage2_agv_sim.py -s

未启用时 skip, 报告中记为"未执行"; 不重复跑全部 dock 组合。
"""

from __future__ import annotations

from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.tasks import (Stage4Task, TaskStatus,
                                           make_task_from_operation)
from flexible_factory.simulation import config as cfg
from stage2_harness import drive, open_context, require_sim, write_evidence


def _phase(coordinator, task, max_wall_s: float) -> dict:
    """驱动协调器直到指定任务结束或现场冻结."""

    def _stop() -> bool:
        stored = coordinator.queue.get(task.task_id)
        return (stored is None or stored.status.terminal
                or coordinator.frozen)

    return drive(coordinator, should_stop=_stop, max_wall_s=max_wall_s)


def test_agv_reposition_then_loaded_transport():
    require_sim()
    with open_context() as ctx:
        coordinator = ctx.new_coordinator()
        catalog = coordinator.catalog

        # ── 1. 空载调机 A→B ────────────────────────────────
        sim_time = coordinator._sim_time()
        reposition = Stage4Task(
            task_id="reposition_uid0_toB_leg1",
            kind=cfg.TASK_KIND_REPOSITION, uid=0, source="A",
            destination="B", dock="B", device="agv",
            status=TaskStatus.PENDING,
            priority=int(cfg.TASK_PRIORITY[cfg.TASK_KIND_REPOSITION]),
            created_sim_time=sim_time)
        assert coordinator.queue.submit(reposition, sim_time)[0]
        assert coordinator._try_start_reposition(reposition, "B", sim_time)
        empty_result = _phase(coordinator, reposition, 300.0)

        # ── 2. 载货 B→A ────────────────────────────────────
        sim_time = coordinator._sim_time()
        ctx.inject_tray_material(coordinator, uid=2,
                                 item_type=ItemType.SEMI_AB)
        transport = make_task_from_operation(
            catalog.operation("transport_BA"), 2, sim_time=sim_time)
        assert coordinator.queue.submit(transport, sim_time)[0]
        assert coordinator._start_transport(transport, "B", "A", sim_time)
        loaded_result = _phase(coordinator, transport, 300.0)

        stored_empty = coordinator.queue.get(reposition.task_id)
        stored_loaded = coordinator.queue.get(transport.task_id)
        payload = {
            "case": "stage2_agv_reposition_and_transport",
            "route": {"start_dock": "B", "end_dock": "A"},
            "empty": empty_result,
            "loaded": loaded_result,
            "empty_task": None if stored_empty is None
            else stored_empty.as_dict(),
            "loaded_task": None if stored_loaded is None
            else stored_loaded.as_dict(),
            "agv_history": list(coordinator.agv_history),
            "agv_at_dock": coordinator.agv_at_dock,
        }
        path = write_evidence("agv", payload)
        print(f"[stage2] agv 证据: {path}")

        assert stored_empty is not None
        assert stored_empty.status is TaskStatus.DONE
        assert stored_loaded is not None
        assert stored_loaded.status is TaskStatus.DONE
        assert coordinator.agv_at_dock == "A"
        assert len(coordinator.agv_history) == 2
        for row in coordinator.agv_history:
            assert row.get("state") == "DONE"
        assert not coordinator.frozen
