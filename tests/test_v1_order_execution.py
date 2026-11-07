"""阶段 3: 编译后的订单图在记录型 coordinator 上端到端执行.

不启动 CoppeliaSim; 执行适配器与几何校验替换为记录型实现. 验证订单输入
按编译 UID 生成、ready 派工、AGV 运输、E 逻辑合流和订单终态/资源释放.
"""

from __future__ import annotations

from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.operation_runtime import TaskStatus
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.default_process import default_catalog
from order_fixtures import full_flow_order
from test_v1_order_failure_isolation import _coordinator, _run

CATALOG = default_catalog()
COMPILER = OrderDagCompiler(CATALOG)


def test_compiled_order_runs_end_to_end_with_recording_executors():
    order = full_flow_order()
    compiled = COMPILER.compile(order, run_id="runorder")
    coordinator = _coordinator(order_plan=compiled)
    coordinator.start(0.0)
    _run(coordinator)

    assert coordinator.order_status() == "SUCCEEDED"
    acceptance = coordinator.acceptance()
    assert acceptance["ok"] is True
    assert acceptance["order"]["order_id"] == order.order_id
    assert acceptance["order_target_quantity"] == 1
    assert coordinator.final_count == 1
    assert [row["uid"] for row in coordinator.spawned] == [1, 2]
    final = coordinator.flow.by_uid(3)
    assert final is not None and final.item_type is ItemType.FINAL
    assert final.parents == (1, 2)
    assert coordinator.flow.join_history[0]["pair_key"] == "1+2"
    join = coordinator.op_runtime.instance("runorder:kit-join:1+2")
    assert join is not None and join.state is TaskStatus.DONE
    assert join.output_uids == [3]
    assert coordinator.summary()["order_plan"]["digest"] == order.dag_digest()
    assert all(owner is None
               for owner in coordinator.resources.owners.values())
    assert [row["kind"] for row in coordinator.events
            if row["kind"] == "FINAL_CREATED"] == ["FINAL_CREATED"]
