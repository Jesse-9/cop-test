"""failures.py — 结构化故障分类与 NODE/GLOBAL 范围 (阶段 2).

分类只读取执行适配器/协调器提供的稳定 ``failure_code``, 不解析错误字符串.
首版是保守 allowlist: 只有发生在物理动作之前、物料位置/账本可核实且资源
已完全释放的节点前置条件拒绝才允许 ``NODE``; 安全锁存、仿真基础设施、
共享资源、未知异常和未准入的 executor 故障一律 ``GLOBAL``.

纯逻辑测试通过不等于授权真实运动错误进入 NODE allowlist; 每个新增
failure code 还需要 focused CoppeliaSim 安全证据 (阶段 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

FAILURE_POLICY_VERSION = "v1_failure_20261106"


class FailureScope(str, Enum):
    NODE = "NODE"
    GLOBAL = "GLOBAL"


class FailureCategory(str, Enum):
    NODE_PRECHECK = "node_precheck"
    EXECUTOR = "executor"
    VERIFICATION = "verification"
    SAFETY = "safety"
    INFRASTRUCTURE = "infrastructure"
    MATERIAL = "material"
    LEDGER = "ledger"
    SHARED_RESOURCE = "shared_resource"
    TIMEOUT = "timeout"
    INTERNAL = "internal"
    OPERATOR = "operator"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureClass:
    code: str
    category: FailureCategory
    default_scope: FailureScope
    requires_safe_confirmation: bool = False
    description: str = ""


FAILURE_CLASSES: dict[str, FailureClass] = {
    "NODE_PRECHECK_REJECTED": FailureClass(
        "NODE_PRECHECK_REJECTED", FailureCategory.NODE_PRECHECK,
        FailureScope.NODE, requires_safe_confirmation=True,
        description="物理动作前的节点前置条件拒绝; 需核实物料/账本/资源"),
    "SAFETY_LATCH": FailureClass(
        "SAFETY_LATCH", FailureCategory.SAFETY, FailureScope.GLOBAL,
        description="安全锁存; 必须停止全场并保留现场"),
    "SIM_STEP_FAILED": FailureClass(
        "SIM_STEP_FAILED", FailureCategory.INFRASTRUCTURE,
        FailureScope.GLOBAL, description="仿真单步异常"),
    "SIM_CONNECTION_LOST": FailureClass(
        "SIM_CONNECTION_LOST", FailureCategory.INFRASTRUCTURE,
        FailureScope.GLOBAL, description="远程 API/会话连接丢失"),
    "SCENE_INTEGRITY_LOST": FailureClass(
        "SCENE_INTEGRITY_LOST", FailureCategory.INFRASTRUCTURE,
        FailureScope.GLOBAL, description="场景完整性丢失"),
    "MATERIAL_STATE_UNKNOWN": FailureClass(
        "MATERIAL_STATE_UNKNOWN", FailureCategory.MATERIAL,
        FailureScope.GLOBAL, description="物料物理位置或状态不可核实"),
    "LEDGER_INCONSISTENT": FailureClass(
        "LEDGER_INCONSISTENT", FailureCategory.LEDGER, FailureScope.GLOBAL,
        description="物料账本与现场不一致或提交结果不确定"),
    "PROCESS_FAILED": FailureClass(
        "PROCESS_FAILED", FailureCategory.LEDGER, FailureScope.GLOBAL,
        description="物料类型转换提交失败"),
    "ZONE_COMMIT_FAILED": FailureClass(
        "ZONE_COMMIT_FAILED", FailureCategory.LEDGER, FailureScope.GLOBAL,
        description="物料区域提交失败"),
    "RESOURCE_RELEASE_DENIED": FailureClass(
        "RESOURCE_RELEASE_DENIED", FailureCategory.SHARED_RESOURCE,
        FailureScope.GLOBAL, description="节点资源无法完整释放"),
    "AGV_ROUTE_FAILED": FailureClass(
        "AGV_ROUTE_FAILED", FailureCategory.SHARED_RESOURCE,
        FailureScope.GLOBAL, description="共享 AGV 航线失败"),
    "SHARED_RESOURCE_FAILED": FailureClass(
        "SHARED_RESOURCE_FAILED", FailureCategory.SHARED_RESOURCE,
        FailureScope.GLOBAL, description="共享执行资源故障"),
    "UNIT_CONTROLLER_FAILURE": FailureClass(
        "UNIT_CONTROLLER_FAILURE", FailureCategory.EXECUTOR,
        FailureScope.GLOBAL, description="单元执行器失败; 首版未准入 NODE"),
    "NODE_VERIFICATION_FAILED": FailureClass(
        "NODE_VERIFICATION_FAILED", FailureCategory.VERIFICATION,
        FailureScope.GLOBAL, description="动作后完成校验失败; 首版未准入"),
    "TASK_TIMEOUT": FailureClass(
        "TASK_TIMEOUT", FailureCategory.TIMEOUT, FailureScope.GLOBAL),
    "BACKPRESSURE_TIMEOUT": FailureClass(
        "BACKPRESSURE_TIMEOUT", FailureCategory.TIMEOUT,
        FailureScope.GLOBAL),
    "STOP_REQUESTED": FailureClass(
        "STOP_REQUESTED", FailureCategory.OPERATOR, FailureScope.GLOBAL,
        description="外部停止请求"),
    "UNKNOWN_EXECUTOR_FAILURE": FailureClass(
        "UNKNOWN_EXECUTOR_FAILURE", FailureCategory.UNKNOWN,
        FailureScope.GLOBAL, description="未知执行器异常"),
    "UNKNOWN_FAILURE": FailureClass(
        "UNKNOWN_FAILURE", FailureCategory.UNKNOWN, FailureScope.GLOBAL),
}

# 首版唯一准入的局部失败 code; 只有同时满足物理动作前/安全状态可核实才生效.
NODE_FAILURE_ALLOWLIST = frozenset({"NODE_PRECHECK_REJECTED"})


def failure_class(code: str) -> FailureClass:
    return FAILURE_CLASSES.get(str(code), FAILURE_CLASSES["UNKNOWN_FAILURE"])


def classify_failure(code: str, *, pre_physical: bool,
                     safe_state_confirmed: bool) -> FailureScope:
    """返回故障范围; 任何不确定输入都收敛到 GLOBAL."""
    normalized = str(code or "UNKNOWN_FAILURE")
    spec = failure_class(normalized)
    if (normalized in NODE_FAILURE_ALLOWLIST
            and spec.default_scope is FailureScope.NODE
            and bool(pre_physical) and bool(safe_state_confirmed)):
        return FailureScope.NODE
    return FailureScope.GLOBAL


@dataclass(frozen=True)
class FailureRecord:
    """一次失败的稳定记录; 只含 ID/时间/诊断, 不含异常对象."""

    failure_code: str
    scope: FailureScope
    message: str = ""
    node_id: str = ""
    instance_id: str = ""
    task_id: str = ""
    device_id: str = ""
    sim_time: float = 0.0
    category: str = ""
    safe_state: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)

    @property
    def is_node(self) -> bool:
        return self.scope is FailureScope.NODE

    def as_dict(self) -> dict:
        return {
            "failure_code": self.failure_code,
            "scope": self.scope.value,
            "message": self.message,
            "node_id": self.node_id,
            "instance_id": self.instance_id,
            "task_id": self.task_id,
            "device_id": self.device_id,
            "sim_time": round(float(self.sim_time), 4),
            "category": self.category,
            "safe_state": dict(self.safe_state),
            "evidence": dict(self.evidence),
        }


def make_failure_record(
        *, code: str, message: str = "", node_id: str = "",
        instance_id: str = "", task_id: str = "", device_id: str = "",
        sim_time: float = 0.0, scope: Optional[FailureScope] = None,
        safe_state: Optional[dict] = None,
        evidence: Optional[dict] = None) -> FailureRecord:
    spec = failure_class(code)
    return FailureRecord(
        failure_code=str(code or spec.code),
        scope=scope if scope is not None else spec.default_scope,
        message=str(message), node_id=str(node_id),
        instance_id=str(instance_id), task_id=str(task_id),
        device_id=str(device_id), sim_time=float(sim_time),
        category=spec.category.value, safe_state=dict(safe_state or {}),
        evidence=dict(evidence or {}))


__all__ = [
    "FAILURE_CLASSES", "FAILURE_POLICY_VERSION", "FailureCategory",
    "FailureClass", "FailureRecord", "FailureScope", "NODE_FAILURE_ALLOWLIST",
    "classify_failure", "failure_class", "make_failure_record",
]
