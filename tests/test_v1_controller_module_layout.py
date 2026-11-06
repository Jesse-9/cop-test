"""控制器模块导航测试: Stage3 实现并入 unit_controller, 旧路径兼容."""

from __future__ import annotations


def test_stage3_controller_and_tray_check_share_one_implementation():
    from flexible_factory.simulation import unit_controller as current
    from flexible_factory.simulation import unit_controller_dual as legacy

    assert legacy.Stage3UnitController is current.Stage3UnitController
    assert legacy.TrayCheck is current.TrayCheck


def test_stage4_still_inherits_stage3_controller():
    from flexible_factory.simulation.unit_controller import (
        Stage3UnitController, Stage4UnitController)

    assert issubclass(Stage4UnitController, Stage3UnitController)
    assert Stage3UnitController.__module__ == Stage4UnitController.__module__
