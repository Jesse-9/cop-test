"""orders.py — 声明式订单契约、规范化序列化与 DAG 摘要 (阶段 1).

本模块只定义**静态订单意图**, 不导入 CoppeliaSim、application 或
persistence, 也不创建任务或运行时句柄. 订单节点只引用
:class:`~flexible_factory.domain.process_model.OperationTemplate` 的稳定
``template_id``; 端口只引用模板端口 identity. 边表达真实物料/工艺前置关系,
不从节点类型或模板 ``depends_on`` 推导隐式依赖.

稳定 `node_id` 与运行期 `instance_id` 分离: 本模块保留声明式节点身份,
:mod:`flexible_factory.domain.order_compiler` 才把它绑定到物料 UID 并生成
运行时实例. 订单版本在运行开始后不可变; 修改编排必须创建新版本.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..util import stable_digest
from .materials import ItemType

# 订单契约版本; 只随字段/语义变化递增, 不依赖场景工件版本.
ORDER_CONTRACT_VERSION = "v1_order_20261106"
ORDER_SOURCES = ("manual", "planner", "fixture")

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class OrderError(RuntimeError):
    code = "ORDER"


class OrderSpecError(OrderError):
    """订单 JSON 结构非法; ``path`` 指向出错字段."""

    code = "ORDER_SPEC"

    def __init__(self, message: str, *, path: str = "") -> None:
        super().__init__(message)
        self.path = str(path)


class OrderValidationError(OrderError):
    """订单结构校验失败; ``diagnostics`` 含稳定 code 和定位."""

    code = "ORDER_VALIDATION"

    def __init__(self, diagnostics: Iterable["OrderDiagnostic"]) -> None:
        self.diagnostics = tuple(diagnostics)
        message = "\n".join(
            f"[{item.code}] {item.message}" for item in self.diagnostics)
        super().__init__(message or "订单结构校验失败")


class OrderCompileError(OrderValidationError):
    """订单无法编译为运行图; 与校验错误共享定位格式."""

    code = "ORDER_COMPILE"


# ═══════════════════════════════════════════════════════════════
# 诊断
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OrderDiagnostic:
    """一条可定位的订单诊断; 不携带异常对象或运行时句柄."""

    code: str
    message: str
    node_id: str = ""
    input_id: str = ""
    edge_index: int = -1
    port_id: str = ""

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "input_id": self.input_id,
            "edge_index": int(self.edge_index),
            "port_id": self.port_id,
        }


# ═══════════════════════════════════════════════════════════════
# 声明式数据模型
# ═══════════════════════════════════════════════════════════════

def _require(condition: bool, message: str, *, path: str = "") -> None:
    if not condition:
        raise OrderSpecError(message, path=path)


def _object(value: Any, *, path: str) -> dict:
    _require(isinstance(value, dict), "必须是 JSON 对象", path=path)
    return value


def _text(value: Any, *, path: str, pattern: Optional[re.Pattern] = None,
          default: Optional[str] = None) -> str:
    if default is not None and (value is None or value == ""):
        return default
    _require(isinstance(value, str) and bool(value.strip()),
             "必须是非空字符串", path=path)
    text = value.strip()
    if pattern is not None:
        _require(bool(pattern.fullmatch(text)),
                 f"标识格式非法: {text!r}", path=path)
    return text


def _integer(value: Any, *, path: str, minimum: int = 0) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool),
             "必须是整数", path=path)
    _require(value >= minimum, f"必须 >= {minimum}", path=path)
    return int(value)


def _check_keys(payload: dict, allowed: set[str], *, path: str) -> None:
    unknown = sorted(set(payload) - allowed)
    _require(not unknown, f"包含未知字段: {unknown}", path=path)


def _item_type(value: Any, *, path: str) -> ItemType:
    text = _text(value, path=path)
    try:
        return ItemType(text)
    except ValueError as exc:
        raise OrderSpecError(f"未知物料类型: {text!r}", path=path) from exc


@dataclass(frozen=True)
class OrderTarget:
    """订单目标产物; 数量按编译后的终端实例数核对."""

    item_type: ItemType
    quantity: int = 1

    def as_dict(self) -> dict:
        return {"item_type": self.item_type.value,
                "quantity": int(self.quantity)}

    @classmethod
    def from_dict(cls, payload: Any, *, path: str = "target") -> "OrderTarget":
        data = _object(payload, path=path)
        _check_keys(data, {"item_type", "quantity"}, path=path)
        return cls(
            item_type=_item_type(data.get("item_type"),
                                 path=f"{path}.item_type"),
            quantity=_integer(data.get("quantity", 1),
                              path=f"{path}.quantity", minimum=1))


@dataclass(frozen=True)
class OrderInput:
    """一笔订单投料; ``node_id``/``port_id`` 可显式绑定入口端口.

    留空时由 validator 按区域 + 物料类型唯一匹配; 无法唯一匹配即诊断.
    """

    input_id: str
    item_type: ItemType
    quantity: int
    zone: str
    node_id: str = ""
    port_id: str = ""

    def as_dict(self) -> dict:
        return {
            "input_id": self.input_id,
            "item_type": self.item_type.value,
            "quantity": int(self.quantity),
            "zone": self.zone,
            "node_id": self.node_id,
            "port_id": self.port_id,
        }

    @classmethod
    def from_dict(cls, payload: Any, *, path: str = "inputs[]") -> "OrderInput":
        data = _object(payload, path=path)
        _check_keys(data, {"input_id", "item_type", "quantity", "zone",
                           "node_id", "port_id"}, path=path)
        return cls(
            input_id=_text(data.get("input_id"), path=f"{path}.input_id",
                           pattern=_ID_PATTERN),
            item_type=_item_type(data.get("item_type"),
                                 path=f"{path}.item_type"),
            quantity=_integer(data.get("quantity", 1),
                              path=f"{path}.quantity", minimum=1),
            zone=_text(data.get("zone"), path=f"{path}.zone"),
            node_id=_text(data.get("node_id"), path=f"{path}.node_id",
                          pattern=_ID_PATTERN, default=""),
            port_id=_text(data.get("port_id"), path=f"{path}.port_id",
                          default=""))


@dataclass(frozen=True)
class OrderNode:
    """订单节点: 稳定 node_id + 已登记模板 ID; 不携带参数或代码."""

    node_id: str
    template_id: str

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "template_id": self.template_id}

    @classmethod
    def from_dict(cls, payload: Any, *, path: str = "nodes[]") -> "OrderNode":
        data = _object(payload, path=path)
        _check_keys(data, {"node_id", "template_id"}, path=path)
        return cls(
            node_id=_text(data.get("node_id"), path=f"{path}.node_id",
                          pattern=_ID_PATTERN),
            template_id=_text(data.get("template_id"),
                              path=f"{path}.template_id"))


@dataclass(frozen=True)
class OrderEdge:
    """一条有向物料/工艺边; ``to_port`` 指向后继输入端口 identity."""

    from_node: str
    to_node: str
    to_port: str = ""
    from_port: str = ""

    def as_dict(self) -> dict:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "to_port": self.to_port,
            "from_port": self.from_port,
        }

    @classmethod
    def from_dict(cls, payload: Any, *, path: str = "edges[]") -> "OrderEdge":
        data = _object(payload, path=path)
        _check_keys(data, {"from", "to", "to_port", "port", "from_port"},
                    path=path)
        _require(not ("to_port" in data and "port" in data),
                 "不能同时提供 to_port 和 port", path=path)
        return cls(
            from_node=_text(data.get("from"), path=f"{path}.from",
                            pattern=_ID_PATTERN),
            to_node=_text(data.get("to"), path=f"{path}.to",
                          pattern=_ID_PATTERN),
            to_port=_text(data.get("to_port", data.get("port")),
                          path=f"{path}.to_port", default=""),
            from_port=_text(data.get("from_port"), path=f"{path}.from_port",
                            default=""))


@dataclass(frozen=True)
class OrderSpec:
    """不可变订单声明; 规范化摘要覆盖全部语义字段."""

    order_id: str
    version: int
    scene_id: str
    target: OrderTarget
    inputs: tuple[OrderInput, ...]
    nodes: tuple[OrderNode, ...]
    edges: tuple[OrderEdge, ...]
    source: str = "manual"
    contract_version: str = ORDER_CONTRACT_VERSION

    # ── 校验/序列化 ─────────────────────────────────────────

    def canonical_dict(self) -> dict:
        """稳定序列化: 容器按稳定 ID 排序, 与输入声明顺序无关."""
        return {
            "contract_version": self.contract_version,
            "order_id": self.order_id,
            "version": int(self.version),
            "scene_id": self.scene_id,
            "source": self.source,
            "target": self.target.as_dict(),
            "inputs": [row.as_dict()
                       for row in sorted(self.inputs,
                                         key=lambda row: row.input_id)],
            "nodes": [row.as_dict()
                      for row in sorted(self.nodes,
                                        key=lambda row: row.node_id)],
            "edges": [row.as_dict()
                      for row in sorted(
                          self.edges,
                          key=lambda row: (row.from_node, row.to_node,
                                           row.to_port, row.from_port))],
        }

    def as_dict(self) -> dict:
        """声明顺序序列化; 用于展示/存储. 摘要用 canonical_dict()."""
        return {
            "contract_version": self.contract_version,
            "order_id": self.order_id,
            "version": int(self.version),
            "scene_id": self.scene_id,
            "source": self.source,
            "target": self.target.as_dict(),
            "inputs": [row.as_dict() for row in self.inputs],
            "nodes": [row.as_dict() for row in self.nodes],
            "edges": [row.as_dict() for row in self.edges],
        }

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))

    def dag_digest(self) -> str:
        return stable_digest(self.canonical_dict())

    def node(self, node_id: str) -> Optional[OrderNode]:
        for row in self.nodes:
            if row.node_id == node_id:
                return row
        return None

    def input(self, input_id: str) -> Optional[OrderInput]:
        for row in self.inputs:
            if row.input_id == input_id:
                return row
        return None

    def with_source(self, source: str) -> "OrderSpec":
        return OrderSpec(
            order_id=self.order_id, version=self.version,
            scene_id=self.scene_id, target=self.target,
            inputs=self.inputs, nodes=self.nodes, edges=self.edges,
            source=str(source), contract_version=self.contract_version)

    @classmethod
    def from_dict(cls, payload: Any, *,
                  path: str = "order") -> "OrderSpec":
        """严格解析; 拒绝未知字段、代码字段和运行时句柄字段."""
        data = _object(payload, path=path)
        _check_keys(data, {
            "contract_version", "order_id", "version", "scene_id", "source",
            "target", "inputs", "nodes", "edges"}, path=path)
        raw_inputs = data.get("inputs")
        raw_nodes = data.get("nodes")
        raw_edges = data.get("edges", [])
        _require(isinstance(raw_inputs, list) and raw_inputs,
                 "inputs 必须是非空数组", path=f"{path}.inputs")
        _require(isinstance(raw_nodes, list) and raw_nodes,
                 "nodes 必须是非空数组", path=f"{path}.nodes")
        _require(isinstance(raw_edges, list), "edges 必须是数组",
                 path=f"{path}.edges")
        source = _text(data.get("source"), path=f"{path}.source",
                       default="manual")
        _require(source in ORDER_SOURCES,
                 f"未知编排来源: {source!r}", path=f"{path}.source")
        contract = _text(data.get("contract_version"),
                         path=f"{path}.contract_version",
                         default=ORDER_CONTRACT_VERSION)
        return cls(
            order_id=_text(data.get("order_id"), path=f"{path}.order_id",
                           pattern=_ID_PATTERN),
            version=_integer(data.get("version", 1),
                             path=f"{path}.version", minimum=1),
            scene_id=_text(data.get("scene_id"), path=f"{path}.scene_id"),
            target=OrderTarget.from_dict(data.get("target"),
                                         path=f"{path}.target"),
            inputs=tuple(
                OrderInput.from_dict(row, path=f"{path}.inputs[{index}]")
                for index, row in enumerate(raw_inputs)),
            nodes=tuple(
                OrderNode.from_dict(row, path=f"{path}.nodes[{index}]")
                for index, row in enumerate(raw_nodes)),
            edges=tuple(
                OrderEdge.from_dict(row, path=f"{path}.edges[{index}]")
                for index, row in enumerate(raw_edges)),
            source=source,
            contract_version=contract)


# ═══════════════════════════════════════════════════════════════
# 运行上下文 (由编译器构造, 供 OperationGraph/coordinator 读取)
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class OrderRunContext:
    """一次订单运行的稳定上下文; 只含 ID/摘要, 不含句柄或实例对象."""

    order_id: str
    order_version: int
    dag_digest: str
    scene_id: str
    node_ids: tuple[str, ...]
    target_item_type: str
    target_quantity: int
    target_instance_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "order_version": int(self.order_version),
            "dag_digest": self.dag_digest,
            "scene_id": self.scene_id,
            "node_ids": list(self.node_ids),
            "target_item_type": self.target_item_type,
            "target_quantity": int(self.target_quantity),
            "target_instance_ids": list(self.target_instance_ids),
        }


__all__ = [
    "ORDER_CONTRACT_VERSION", "ORDER_SOURCES", "OrderDiagnostic",
    "OrderCompileError", "OrderEdge", "OrderError", "OrderInput",
    "OrderNode", "OrderRunContext", "OrderSpec", "OrderSpecError",
    "OrderTarget", "OrderValidationError",
]
