"""阶段 2 步骤 5: 传送带边界 (不需要 CoppeliaSim).

06-20 明确本阶段不虚构"逐 UID 输送 executor"或 `conveyor_transport`
capability: 传送带仍是共享线资源 + 底层 motor command facade, 由机械臂
工序在需要时启停。本测试固定该边界, 防止适配层悄悄扩张职责。
"""

from __future__ import annotations

import ast
from pathlib import Path

from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.rules import RESOURCE_ORDER
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation import config_base as base
from flexible_factory.simulation import execution
from flexible_factory.simulation.execution import sink_spec_for
from flexible_factory.simulation.layout import line_specs

SRC = Path(__file__).resolve().parent.parent / "src"
EXECUTION_DIR = SRC / "flexible_factory" / "simulation" / "execution"
CATALOG = default_catalog()


def test_execution_layer_has_no_conveyor_executor():
    names = {path.name for path in EXECUTION_DIR.glob("*.py")}
    assert "conveyor.py" not in names
    assert not hasattr(execution, "ConveyorExecutor")
    assert not hasattr(execution, "ConveyorTransportExecutor")


def test_catalog_declares_no_conveyor_transport_capability():
    capabilities = {spec.id for spec in CATALOG.capabilities}
    assert capabilities == {"robotic_pick_place", "agv_transport",
                            "logical_join"}
    assert "conveyor_transport" not in capabilities


def test_line_resources_are_held_by_operations_not_executor():
    """线仍是资源表条目, 由工序资源请求持有."""
    for line_id in cfg.LINE_IDS:
        assert line_id in RESOURCE_ORDER
    holders = [operation.id for recipe in CATALOG.recipes
               for operation in recipe.operations
               if any(line_id in operation.resources
                      for line_id in cfg.LINE_IDS)]
    assert holders, "至少有一个工序必须声明线资源"

    for recipe in CATALOG.recipes:
        for operation in recipe.operations:
            for resource in operation.resources:
                if resource in cfg.LINE_IDS:
                    assert operation.executor_kind == "arm"


def test_conveyor_sink_is_geometry_only():
    """放置汇只描述几何与线 ID, 不携带可执行命令."""
    lines = line_specs()
    sink = sink_spec_for(CATALOG.operation("housing_finish"), "C", None,
                         lines)
    assert sink.kind == "conveyor"
    assert sink.zone == "line_ce"
    assert sink.line is lines["line_ce"]
    assert sink.support_z == base.CONVEYOR_SURFACE_Z
    fields = set(vars(sink))
    assert not {"run", "stop", "speed"} & fields


def test_execution_modules_do_not_touch_conveyor_directly():
    """适配层不直接调速: 启停仍由单元控制器按工序执行."""
    offenders: list[str] = []
    for path in EXECUTION_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {
                    "setSpeed", "setConveyorSpeed", "start", "stop"}:
                offenders.append(f"{path.name}:{node.lineno}:{node.attr}")
    assert not offenders, f"adapter 不得直接驱动传送带: {offenders}"
