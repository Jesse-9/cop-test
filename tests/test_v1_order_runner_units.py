"""订单运行器工位子集辅助函数测试 (纯逻辑, 不连接 CoppeliaSim)."""

from __future__ import annotations

from types import SimpleNamespace

from flexible_factory.application.order_runner import required_arm_units


def _compiled(*rows):
    return SimpleNamespace(instances=list(rows))


def _instance(candidates=(), selected=""):
    return SimpleNamespace(candidate_device_ids=list(candidates),
                           selected_device_id=selected)


def test_required_arm_units_uses_candidate_union():
    compiled = _compiled(
        _instance(["arm_C", "arm_A"]),
        _instance(["arm_C", "arm_D"]),
        _instance(["agv"]),
    )
    assert required_arm_units(compiled) == ("A", "C", "D")


def test_required_arm_units_includes_selected_device():
    compiled = _compiled(_instance([], selected="arm_B"))
    assert required_arm_units(compiled) == ("B",)


def test_required_arm_units_empty_for_logic_only_order():
    compiled = _compiled(_instance(["agv"]))
    assert required_arm_units(compiled) == ()


def test_required_arm_units_stable_unit_order():
    compiled = _compiled(_instance(["arm_E", "arm_B", "arm_D"]))
    assert required_arm_units(compiled) == ("B", "D", "E")