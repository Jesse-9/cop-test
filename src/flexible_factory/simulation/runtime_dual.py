"""runtime_dual.py — 旧导入路径兼容层.

Stage3 runtime builder、共享容器类型和 Stage3 构建错误已并入
:mod:`flexible_factory.simulation.runtime` / :mod:`runtime_common`. 本文件
只 re-export, 不复制实现.

`RuntimeBuildError` 是旧名别名, 现在指向
:class:`flexible_factory.simulation.runtime.Stage3RuntimeBuildError`;
错误码仍为 ``STAGE3_RUNTIME``.

删除条件: 全仓库及外部脚本不再导入 ``runtime_dual`` 时, 删除本文件并把
调用方改为从 ``runtime`` / ``runtime_common`` 导入. `Stage3*` 类名与
控制器 ID 不变.
"""

from __future__ import annotations

from .runtime import Stage3RuntimeBuildError, build_stage3_runtime
from .runtime_common import Stage3ArmRuntime, Stage3Runtime, UnitSceneView

# 旧异常名兼容: 实现与错误码由 Stage3RuntimeBuildError 提供.
RuntimeBuildError = Stage3RuntimeBuildError

__all__ = [
    "RuntimeBuildError",
    "Stage3ArmRuntime",
    "Stage3Runtime",
    "Stage3RuntimeBuildError",
    "UnitSceneView",
    "build_stage3_runtime",
]
