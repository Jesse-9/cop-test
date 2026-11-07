"""订单 DAG 测试夹具: 当前 full_flow 的可编译模板订单 (阶段 1).

`full_flow_order` 选择壳体直达 A 路线 + 支架 D→E 路线, 两条分支在 E 岛
由已登记的 ``kit_join`` 合流为 FINAL. 它与当前默认工艺共享模板、端口和
``commit_join`` 语义; RAW_B 绕行路线是同一``housing_supply``互斥组中的另一
选择, 不在同一订单里重复表达 (否则会产生未配对 SEMI_CE 悬空分支).
"""

from __future__ import annotations

from flexible_factory.domain.materials import ItemType
from flexible_factory.domain.orders import (OrderEdge, OrderInput, OrderNode,
                                            OrderSpec, OrderTarget)


def full_flow_order(*, order_id: str = "order-full-flow-001",
                    version: int = 1, raw_a: int = 1,
                    raw_d: int = 1, target_quantity: int = 1,
                    source: str = "fixture") -> OrderSpec:
    """构造与 full_flow 直达分支等价的订单声明."""
    return OrderSpec(
        order_id=order_id, version=int(version), scene_id="L5_UFLOW",
        target=OrderTarget(ItemType.FINAL, int(target_quantity)),
        inputs=(
            OrderInput(input_id="raw-a-1", item_type=ItemType.RAW_A,
                       quantity=int(raw_a), zone="line_s1a",
                       node_id="housing-start", port_id="input-0"),
            OrderInput(input_id="raw-d-1", item_type=ItemType.RAW_D,
                       quantity=int(raw_d), zone="line_s2d",
                       node_id="bracket-start", port_id="input-0"),
        ),
        nodes=(
            OrderNode("housing-start", "housing_direct_A"),
            OrderNode("housing-finish", "housing_finish"),
            OrderNode("housing-buffer", "housing_to_E_buffer"),
            OrderNode("bracket-start", "bracket_process"),
            OrderNode("bracket-transport", "transport_DE"),
            OrderNode("bracket-buffer", "bracket_to_E"),
            OrderNode("kit-join", "kit_join"),
        ),
        edges=(
            OrderEdge("housing-start", "housing-finish", "input-0"),
            OrderEdge("housing-finish", "housing-buffer", "input-0"),
            OrderEdge("bracket-start", "bracket-transport", "input-0"),
            OrderEdge("bracket-transport", "bracket-buffer", "input-0"),
            OrderEdge("housing-buffer", "kit-join", "housing-input"),
            OrderEdge("bracket-buffer", "kit-join", "bracket-input"),
        ),
        source=source)


__all__ = ["full_flow_order"]
