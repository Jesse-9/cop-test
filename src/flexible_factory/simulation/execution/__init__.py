"""execution — 设备执行适配层 (06-20 阶段 2).

只提供机械臂与 AGV 两种执行适配器；E 逻辑配对仍由协调器
``_check_join`` 与 ``MaterialRegistry`` 负责, 不复制配对规则。

依赖约束: 本包不导入 ``application`` / ``cli``, 不调用 ``sim.step()``,
不写任务队列、物料谱系或资源表。
"""

from __future__ import annotations

from .agv import AgvExecutor
from .arm import ArmExecutor, sink_spec_for
from .base import (ExecutionRequest, ExecutionReport, ExecutionState,
                   Executor, ExecutorError, evidence_ok, make_execution_id)

__all__ = [
    "AgvExecutor",
    "ArmExecutor",
    "ExecutionRequest",
    "ExecutionReport",
    "ExecutionState",
    "Executor",
    "ExecutorError",
    "evidence_ok",
    "make_execution_id",
    "sink_spec_for",
]
