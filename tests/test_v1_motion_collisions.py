"""v1 运动层碰撞查询适配与集合策略测试."""

import pytest

from flexible_factory.simulation.motion.collisions import (
    CollisionRuntime, QueryState, _add_items_to_collection,
    parse_collision_result, parse_distance_result)


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


class _CollectionSim:
    handle_single = 0

    def __init__(self, *, batch=True, silent=False, raise_batch=False):
        self.items = set()
        self.batch_calls = 0
        self.item_calls = 0
        self.silent = silent
        self.raise_batch = raise_batch
        if not batch:
            self.addItemsToCollection = None

    def getCollectionObjects(self, collection):
        return list(self.items)

    def addItemsToCollection(self, collection, what, handles, options):
        self.batch_calls += 1
        if self.raise_batch:
            raise RuntimeError("batch unavailable")
        if not self.silent:
            self.items.update(int(h) for h in handles)

    def addItemToCollection(self, collection, what, handle, options):
        self.item_calls += 1
        self.items.add(int(handle))


def test_add_items_to_collection_uses_batch_api():
    sim = _CollectionSim()
    _add_items_to_collection(sim, 1, [10, 11, 12, 11])
    assert sim.items == {10, 11, 12}
    assert sim.batch_calls == 1
    assert sim.item_calls == 0


def test_add_items_to_collection_falls_back_when_batch_raises():
    sim = _CollectionSim(raise_batch=True)
    _add_items_to_collection(sim, 1, [10, 11, 12])
    assert sim.items == {10, 11, 12}
    assert sim.item_calls == 3


def test_add_items_to_collection_repairs_silent_batch():
    sim = _CollectionSim(silent=True)
    _add_items_to_collection(sim, 1, [10, 11, 12])
    assert sim.items == {10, 11, 12}
    assert sim.item_calls == 3


def test_add_items_to_collection_falls_back_without_batch_api():
    sim = _CollectionSim(batch=False)
    _add_items_to_collection(sim, 1, [10, 11, 12])
    assert sim.items == {10, 11, 12}
    assert sim.item_calls == 3
