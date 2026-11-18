"""双车运输调度策略的离线测试 (不需要 CoppeliaSim).

锁住四件事:
  1. 路网/路线/资源推导与 v1 的 AGV 图一致;
  2. 资源模型是"整条路线全有或全无", 天然无死锁;
  3. 双车相对单 AGV 的吞吐收益, 以及固定分工在车辆故障时停摆;
  4. 模块不依赖仿真层/应用层, 可纯逻辑回归。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from flexible_factory.simulation import transport_schedule as ts


# ═══════════════════════════════════════════════════════════════
# 1. 路网与路线
# ═══════════════════════════════════════════════════════════════

def test_graph_matches_v1_agv_graph():
    assert set(ts.NODES) == {"A", "B", "D", "E", "J_SW", "J_SE"}
    assert ts.NODES["A"] == (0.40, 0.70)
    assert ts.NODES["E"] == (2.90, 1.35)
    assert len(ts.EDGES) == 5
    assert set(ts.LANES) == {"agv_lane_west", "agv_lane_south",
                             "agv_lane_east"}


def test_short_routes_use_single_lane():
    route = ts.plan_route("B", "A")
    assert route.nodes == ("B", "A")
    assert route.lanes == ("agv_lane_west",)
    assert route.length == pytest.approx(1.40)
    assert route.turns == 0

    route = ts.plan_route("D", "E")
    assert route.lanes == ("agv_lane_east",)
    assert route.length == pytest.approx(2.70)


def test_cross_routes_traverse_three_lanes():
    route = ts.plan_route("A", "D")
    assert route.nodes == ("A", "B", "J_SW", "J_SE", "D")
    assert route.lanes == ("agv_lane_west", "agv_lane_south",
                           "agv_lane_east")
    assert route.length == pytest.approx(6.05)
    assert route.turns == 3

    assert ts.plan_route("A", "E").length == pytest.approx(8.75)


def test_route_resources_include_lanes_and_both_docks():
    route = ts.plan_route("B", "A")
    assert route.resources() == frozenset(
        {"agv_lane_west", "B", "A"})


def test_unknown_node_is_rejected():
    with pytest.raises(ValueError):
        ts.plan_route("A", "Z")


# ═══════════════════════════════════════════════════════════════
# 2. 车辆参数
# ═══════════════════════════════════════════════════════════════

def test_cart2_is_slower_than_agv():
    route = ts.plan_route("D", "E")
    assert ts.CART_2.speed < ts.AGV_1.speed
    assert ts.CART_2.travel_time(route) > ts.AGV_1.travel_time(route)


def test_travel_time_includes_turns_and_speed():
    route = ts.plan_route("B", "A")
    expected = 1.40 / ts.AGV_1.speed + 0 * ts.AGV_1.turn_time
    assert ts.AGV_1.travel_time(route) == pytest.approx(expected)

    cross = ts.plan_route("A", "D")
    expected = 6.05 / ts.AGV_1.speed + 3 * ts.AGV_1.turn_time
    assert ts.AGV_1.travel_time(cross) == pytest.approx(expected)


def test_eta_prefers_the_nearer_vehicle():
    job = ts.TransportJob("j", "PART_DE", "D", "E")
    near = ts.eta_for(ts.CART_2, "D", job)
    far = ts.eta_for(ts.CART_2, "A", job)
    assert near < far
    assert far - near > 60.0        # A→D 空载约 66 s


# ═══════════════════════════════════════════════════════════════
# 3. 策略
# ═══════════════════════════════════════════════════════════════

def test_policy_filters_vehicles_by_job_category():
    housing = ts.TransportJob("h", "SEMI_AB", "B", "A")
    bracket = ts.TransportJob("b", "PART_DE", "D", "E")
    both = [ts.AGV_1, ts.CART_2]

    assert [v.vehicle_id for v in ts.eligible_vehicles("single", housing, both)] \
        == ["agv_1"]
    assert [v.vehicle_id for v in ts.eligible_vehicles("fixed", housing, both)] \
        == ["agv_1"]
    assert [v.vehicle_id for v in ts.eligible_vehicles("fixed", bracket, both)] \
        == ["cart_2"]
    assert len(ts.eligible_vehicles("eta", housing, both)) == 2


def test_balanced_batch_runs_both_vehicles_in_parallel():
    jobs = ts.default_jobs(1, 1)
    result = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                  policy="eta").run()
    assert result["completed"] == 2
    assert result["pending"] == 0
    # 两车都用上了, 且总完工时间由较慢的小车二决定 (约 31 s)
    assert all(value > 0.0 for value in result["utilization"].values())
    assert result["makespan_s"] < 40.0


def test_dual_beats_single_on_balanced_batch():
    jobs = ts.default_jobs(3, 3)
    single = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                  policy="single").run()
    fixed = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                 policy="fixed").run()
    assert fixed["makespan_s"] < single["makespan_s"] * 0.75
    assert fixed["completed"] == single["completed"] == 6


def test_fixed_policy_starves_when_dedicated_vehicle_is_down():
    jobs = ts.default_jobs(3, 3)
    fixed = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                 policy="fixed",
                                 unavailable=("cart_2",)).run()
    eta = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                               policy="eta",
                               unavailable=("cart_2",)).run()
    assert fixed["completed"] == 3 and fixed["pending"] == 3
    assert eta["completed"] == 6 and eta["pending"] == 0


def test_all_jobs_complete_and_no_deadlock():
    jobs = ts.default_jobs(10, 10)
    result = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                  policy="eta").run()
    assert result["completed"] == result["jobs"] == 20
    assert result["pending"] == 0
    assert result["makespan_s"] > 0.0


def test_route_level_reservation_serialises_shared_lane():
    """两单都从 A 出发走西直道时, 必须串行, 不可能同时占用。"""
    jobs = [
        ts.TransportJob("x", "SEMI_AB", "B", "A", created_at=0.0),
        ts.TransportJob("y", "SEMI_AB", "B", "A", created_at=0.0),
    ]
    result = ts.DualCartSimulator([ts.AGV_1, ts.CART_2], jobs,
                                  policy="eta").run()
    starts = [e["t"] for e in result["events"] if e["event"] == "start"]
    assert len(starts) == 2
    assert starts[1] > starts[0] + 10.0     # 第二单必须等第一单腾出 A/B


def test_priority_breaks_ties_for_equal_jobs():
    low = ts.TransportJob("low", "PART_DE", "D", "E", priority=1)
    high = ts.TransportJob("high", "PART_DE", "D", "E", priority=3)
    result = ts.DualCartSimulator([ts.CART_2], [low, high],
                                  policy="eta").run()
    first = [e["job"] for e in result["events"] if e["event"] == "start"][0]
    assert first == "high"


# ═══════════════════════════════════════════════════════════════
# 4. 边界
# ═══════════════════════════════════════════════════════════════

def test_module_does_not_import_simulation_or_application():
    """纯逻辑模块: 不许导入场景/应用层 (否则离线测试就失去意义)。"""
    source = pathlib.Path(ts.__file__).read_text(encoding="utf-8")
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
    assert "scene" not in imported
    assert "application" not in imported
    assert "persistence" not in imported


def test_compare_policies_returns_all_three():
    result = ts.compare_policies([ts.AGV_1, ts.CART_2], ts.default_jobs(2, 2))
    assert set(result) == set(ts.POLICIES)
    assert result["single"]["makespan_s"] >= result["eta"]["makespan_s"]
    assert result["single"]["makespan_vs_single"] == pytest.approx(1.0)
