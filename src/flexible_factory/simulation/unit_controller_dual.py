"""unit_controller_dual.py — 旧导入路径兼容层.

`Stage3UnitController` 与 `TrayCheck` 的实现已并入
:mod:`flexible_factory.simulation.unit_controller`, 与 UFLOW 层控制器位于
同一导航模块. 本文件只 re-export, 不复制实现.

删除条件: 全仓库及外部脚本不再导入 ``unit_controller_dual`` 时, 删除本
文件并把调用方改为从 ``unit_controller`` 导入. 类名和控制器 ID 不变.
"""

from __future__ import annotations

from .unit_controller import Stage3UnitController, TrayCheck

__all__ = ["Stage3UnitController", "TrayCheck"]
