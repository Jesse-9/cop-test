"""v1 运动层碰撞查询适配与集合策略测试."""

import pytest

from flexible_factory.simulation.motion.collisions import (CollisionRuntime, QueryState,
                               parse_collision_result,
                               parse_distance_result)


def test_parse_collision_overlap():
    result = parse_collision_result("x", (1, [10, 20]))
    assert result.state is QueryState.COLLISION
    assert result.pair == (10, 20)


def test_parse_collision_clear():
    result = parse_collision_result("x", (0, [-1, -1]))
    assert result.state is QueryState.CLEAR


def test_parse_collision_unknown_forms():
    assert parse_collision_result("x", None).state is QueryState.UNKNOWN
    assert parse_collision_result("x", ("bad", [1, 2])).state is \
        QueryState.UNKNOWN
    assert parse_collision_result("x", (7, [1, 2])).state is QueryState.UNKNOWN


def test_parse_distance_within_threshold():
    result = parse_distance_result(
        "x", (1, [0, 0, 0, 0.03, 0, 0, 0.03], [1, 2]), 0.05)
    assert result.state is QueryState.NEAR
    assert result.distance == pytest.approx(0.03)


def test_parse_distance_outside_threshold_is_clear_not_zero():
    result = parse_distance_result(
        "x", (0, [0.0] * 7, [-1, -1]), 0.01)
    assert result.state is QueryState.CLEAR
    assert result.distance is None


def test_parse_distance_malformed_is_unknown():
    assert parse_distance_result("x", None, 0.1).state is QueryState.UNKNOWN
    assert parse_distance_result("x", (1, [0, 0], [1, 2]), 0.1).state is \
        QueryState.UNKNOWN
    assert parse_distance_result(
        "x", (1, [0, 0, 0, 0, 0, 0, float("nan")], [1, 2]),
        0.1).state is QueryState.UNKNOWN


def test_self_pairs_exclude_adjacent_links():
    links = list(range(100, 107))
    pairs = CollisionRuntime._build_self_pairs(links)
    assert len(pairs) == 15
    for a, b in pairs:
        assert abs(links.index(a) - links.index(b)) >= 2


def test_distance_margin_zero_means_no_threshold_documented():
    # result=0 + 全零 data 只表示阈值内无近邻; 不能解释为距离 0
    result = parse_distance_result("x", (0, [0.0] * 7, [-1, -1]), 0.005)
    assert result.ok
    assert result.state is QueryState.CLEAR
