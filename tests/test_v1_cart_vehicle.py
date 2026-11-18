"""小车二物理车（simulation/cart_vehicle.py）的离线测试.

用 test_v1_cart 里的 FakeSim 记录 API 调用，不需要 CoppeliaSim。
重点锁住：零件表、建造、行驶位姿、演示结束落位、以及"不引入外层依赖"。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from test_v1_cart import FakeSim

from flexible_factory.simulation import cart_vehicle as cv
from flexible_factory.simulation.transport_schedule import NODES, plan_route


# ═══════════════════════════════════════════════════════════════
# 零件表与建造
# ═══════════════════════════════════════════════════════════════

def test_parts_cover_root_deck_tray_and_four_wheels():
    parts = cv.cart2_parts()
    aliases = [part["alias"] for part in parts]
    assert aliases[0] == cv.CART2_ROOT
    assert len(aliases) == len(set(aliases)) == 7
    assert aliases.count("Cart2_Wheel_FL") == 1
    for alias in aliases:
        assert alias == "Cart2" or alias.startswith("Cart2_")
        assert "/" not in alias


def test_build_places_vehicle_at_home_node():
    sim = FakeSim()
    result = cv.build_cart2(sim, cv.Cart2Spec(node="D"))
    assert result["ok"] is True
    assert result["object_count"] == 7
    root = result["root_handle"]
    position = sim.getObjectPosition(root)
    assert position[0] == pytest.approx(NODES["D"][0])
    assert position[1] == pytest.approx(NODES["D"][1])
    # 所有子 shape 都是 static + respondable
    for alias in result["aliases"]:
        handle = next(h for h, o in sim.objects.items() if o["alias"] == alias)
        if handle == root:
            continue
        assert sim.getObjectInt32Param(handle, sim.shapeintparam_static) == 1
        assert sim.getObjectInt32Param(
            handle, sim.shapeintparam_respondable) == 1


def test_build_refuses_while_running():
    sim = FakeSim()
    sim.state = sim.simulation_running
    with pytest.raises(cv.Cart2StateError):
        cv.build_cart2(sim)


def test_build_refuses_duplicate_and_replace_works():
    sim = FakeSim()
    cv.build_cart2(sim)
    with pytest.raises(cv.Cart2StateError):
        cv.build_cart2(sim)
    result = cv.build_cart2(sim, replace=True)
    assert result["replaced_previous"]["removed"] == 7
    assert len(cv.cart2_objects(sim)) == 7


# ═══════════════════════════════════════════════════════════════
# 行驶
# ═══════════════════════════════════════════════════════════════

def test_drive_route_ends_at_destination_and_spends_turns():
    sim = FakeSim()
    cv.build_cart2(sim, cv.Cart2Spec(node="D"))
    route = plan_route("D", "E")
    result = sim and cv.drive_route(sim, route)
    assert result["ok"] is True
    assert result["steps"] > 500            # 2.7 m / 0.1 m/s / 0.05 s ≈ 540
    position = result["pose"]
    assert position[0] == pytest.approx(NODES["E"][0], abs=1e-6)
    assert position[1] == pytest.approx(NODES["E"][1], abs=1e-6)
    assert sim.steps == result["steps"]


def test_demo_restores_destination_pose_after_stop():
    """CoppeliaSim 停仿真会复位场景; demo 必须把终点位姿补回来。"""
    sim = FakeSim()
    cv.build_cart2(sim, cv.Cart2Spec(node="D"))
    result = cv.demo_route(sim, "D", "E")
    assert result["ok"] is True
    position = result["pose"]
    assert position[0] == pytest.approx(NODES["E"][0], abs=1e-6)
    assert position[1] == pytest.approx(NODES["E"][1], abs=1e-6)
    assert sim.state == sim.simulation_stopped


def test_demo_requires_existing_vehicle():
    sim = FakeSim()
    with pytest.raises(cv.Cart2StateError):
        cv.demo_route(sim)


# ═══════════════════════════════════════════════════════════════
# 边界
# ═══════════════════════════════════════════════════════════════

def test_module_does_not_import_application_or_persistence():
    source = pathlib.Path(cv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
    assert not ({"application", "persistence", "cli"} & imported), imported
