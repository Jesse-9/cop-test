"""附加件小车 (simulation/cart.py) 的离线测试.

这些测试不需要 CoppeliaSim: 用一个最小的 ``FakeSim`` 记录 API 调用,
验证"只新增、不越界"的三条硬约束:

  1. 零件表与别名都在 ``WorkshopCart`` 前缀下, 不会碰既有对象;
  2. 默认停放点与既有布局几何保持间隙, 且完全落在地面内;
  3. 增删/演示只操作小车对象, 且不改动 ``config`` / ``artifacts`` /
     ``application`` 任一层的状态或导入面.
"""

from __future__ import annotations

import ast
import math
import pathlib

import pytest

from flexible_factory.simulation import cart as cart_mod
from flexible_factory.simulation.layout import default_layout
from flexible_factory.simulation.layout_base import overlap_rects

_FOREIGN_ALIAS = "Island_A"


class FakeSim:
    """只实现 cart.py 实际用到的 API；所有状态可断言。"""

    # ── 常量（值必须自洽，具体数字不重要）───────────────────
    primitiveshape_cuboid = 0
    primitiveshape_cylinder = 1
    handle_world = -1
    handle_scene = 0
    colorcomponent_ambient_diffuse = 0

    shapeintparam_static = 10
    shapeintparam_kinematic = 11
    shapeintparam_respondable = 12
    shapeintparam_respondable_mask = 13
    shapefloatparam_mass = 20

    simulation_stopped = 0
    simulation_stepping = 1
    simulation_running = 2

    object_shape_type = 100
    object_dummy_type = 101
    object_joint_type = 102
    object_proximitysensor_type = 103
    object_visionsensor_type = 104
    object_graph_type = 105
    object_light_type = 106
    object_camera_type = 107
    object_mirror_type = 108
    object_octree_type = 109
    object_pointcloud_type = 110
    object_forcesensor_type = 111
    object_script_type = 112

    def __init__(self) -> None:
        self.objects: dict[int, dict] = {}
        self._next = 1000
        self.state = self.simulation_stopped
        self.steps = 0
        self.stepping = False
        self.removed: list[list[int]] = []

    # ── 创建 ────────────────────────────────────────────────
    def _new(self, object_type: int, **extra) -> int:
        handle = self._next
        self._next += 1
        self.objects[handle] = {
            "type": object_type,
            "alias": "",
            "parent": -1,
            "position": [0.0, 0.0, 0.0],
            "orientation": [0.0, 0.0, 0.0],
            "int_params": {},
            "color": None,
            **extra,
        }
        return handle

    def createPrimitiveShape(self, shape_type: int, size) -> int:
        return self._new(self.object_shape_type, shape_kind=shape_type,
                         size=[float(v) for v in size])

    def createDummy(self, size) -> int:
        return self._new(self.object_dummy_type, size=float(size))

    # ── 属性 ────────────────────────────────────────────────
    def setObjectAlias(self, handle: int, alias: str) -> None:
        self.objects[handle]["alias"] = alias

    def getObjectAlias(self, handle: int) -> str:
        return self.objects[handle]["alias"]

    def setObjectParent(self, child: int, parent: int, keep: bool = True):
        self.objects[child]["parent"] = int(parent)

    def getObjectParent(self, handle: int) -> int:
        return int(self.objects[handle]["parent"])

    def setObjectPosition(self, handle: int, position, relative_to: int = -1):
        self.objects[handle]["position"] = [float(v) for v in position]
        self.objects[handle]["position_ref"] = int(relative_to)

    def getObjectPosition(self, handle: int, relative_to: int = -1):
        return list(self.objects[handle]["position"])

    def setObjectOrientation(self, handle: int, orientation,
                             relative_to: int = -1):
        self.objects[handle]["orientation"] = [float(v) for v in orientation]

    def getObjectOrientation(self, handle: int, relative_to: int = -1):
        return list(self.objects[handle]["orientation"])

    def setObjectColor(self, handle: int, component: int, color_component: int,
                       color) -> None:
        self.objects[handle]["color"] = [float(v) for v in color]

    def getObjectColor(self, handle: int):
        return self.objects[handle].get("color")

    def setObjectInt32Param(self, handle: int, param: int, value: int):
        self.objects[handle]["int_params"][int(param)] = int(value)

    def getObjectInt32Param(self, handle: int, param: int) -> int:
        return int(self.objects[handle]["int_params"].get(int(param), 0))

    def setObjectFloatParam(self, handle: int, param: int, value: float):
        self.objects[handle]["int_params"][int(param)] = float(value)

    def getObjectFloatParam(self, handle: int, param: int) -> float:
        return float(self.objects[handle]["int_params"].get(int(param), 0.0))

    def getFloatProperty(self, handle: int, prop: str):
        raise RuntimeError("friction property unavailable")

    def resetDynamicObject(self, handle: int) -> None:
        return None

    # ── 枚举 / 删除 ─────────────────────────────────────────
    def getObjects(self, index: int, object_type: int) -> int:
        handles = [h for h, o in self.objects.items()
                   if o["type"] == int(object_type)]
        if index < len(handles):
            return handles[index]
        return -1

    def removeObjects(self, handles) -> None:
        listed = [int(h) for h in handles]
        self.removed.append(listed)
        for handle in listed:
            self.objects.pop(handle, None)
        # 删父对象会带走子对象
        for handle in list(self.objects):
            parent = self.objects[handle]["parent"]
            while parent >= 0:
                if parent in listed:
                    self.objects.pop(handle, None)
                    break
                parent = self.objects.get(parent, {}).get("parent", -1)

    # ── 仿真状态 ────────────────────────────────────────────
    def getSimulationState(self) -> int:
        return self.state

    def setStepping(self, value: bool) -> None:
        self.stepping = bool(value)

    def startSimulation(self) -> None:
        self.state = self.simulation_running

    def stopSimulation(self) -> None:
        self.state = self.simulation_stopped

    def step(self) -> None:
        self.steps += 1


def _seed_foreign_object(sim: FakeSim) -> int:
    handle = sim._new(sim.object_shape_type, shape_kind=0,
                      size=[0.6, 0.6, 0.3])
    sim.setObjectAlias(handle, _FOREIGN_ALIAS)
    sim.setObjectInt32Param(handle, sim.shapeintparam_static, 1)
    return handle


# ═══════════════════════════════════════════════════════════════
# 1. 零件表与别名
# ═══════════════════════════════════════════════════════════════

def test_parts_are_prefixed_and_unique():
    parts = cart_mod.cart_parts()
    aliases = [part["alias"] for part in parts]
    assert aliases[0] == cart_mod.CART_ROOT_ALIAS
    assert len(aliases) == len(set(aliases)) == 8
    for alias in aliases:
        assert alias == cart_mod.CART_ROOT_ALIAS or \
            alias.startswith(cart_mod.CART_PREFIX + "_")
        # 别名必须扁平: 不允许 '/' 或空格
        assert "/" not in alias and " " not in alias
    kinds = [part["kind"] for part in parts]
    assert kinds.count("dummy") == 1
    assert kinds.count("cylinder") == 4
    assert kinds.count("cuboid") == 3


def test_parts_share_one_root_and_stay_above_floor():
    parts = cart_mod.cart_parts()
    for part in parts[1:]:
        assert part["local_pos"][2] > 0.0
        assert part["size"][0] > 0.0 and part["size"][1] > 0.0


# ═══════════════════════════════════════════════════════════════
# 2. 停放点与既有几何的间隙
# ═══════════════════════════════════════════════════════════════

def test_default_spot_is_clear():
    layout = default_layout()
    spec = cart_mod.default_cart_spec(layout)
    report = cart_mod.clearance_report(spec, layout)
    assert report["ok"] is True
    assert report["inside_floor"] is True
    assert report["blocked_by"] == []
    assert report["nearest_gap"] >= cart_mod.CLEARANCE_GAP


def test_default_spot_does_not_intersect_any_obstacle():
    layout = default_layout()
    spec = cart_mod.default_cart_spec(layout)
    for name, rect in cart_mod.obstacle_rects(layout):
        assert not overlap_rects(spec.footprint, rect), name


def test_default_spot_is_north_of_output_table_and_east_of_agv_lane():
    layout = default_layout()
    spec = cart_mod.default_cart_spec(layout)
    table = layout.e_output_table_rect
    lane = layout.agv_lane_rects
    # 输出台在 y 方向更靠南, 小车在其北侧
    assert spec.footprint[1] > table[3]
    # 东直道完全在小车西侧
    east = [rect for name, rect in lane.items() if name.startswith("E")]
    for rect in east:
        assert spec.footprint[0] > rect[2]


def test_rotated_footprint_swaps_extents():
    straight = cart_mod.CartSpec(x=0.0, y=0.0, yaw=0.0)
    turned = cart_mod.CartSpec(x=0.0, y=0.0, yaw=math.pi / 2.0)
    w0 = straight.footprint[2] - straight.footprint[0]
    h0 = straight.footprint[3] - straight.footprint[1]
    w1 = turned.footprint[2] - turned.footprint[0]
    h1 = turned.footprint[3] - turned.footprint[1]
    assert w0 == pytest.approx(h1)
    assert h0 == pytest.approx(w1)


def test_clearance_flags_collision_inside_e_island():
    layout = default_layout()
    spec = cart_mod.CartSpec(x=2.34, y=1.35)      # E 岛中心
    report = cart_mod.clearance_report(spec, layout)
    assert report["ok"] is False
    assert any(item["name"] == "island:E" for item in report["blocked_by"])


# ═══════════════════════════════════════════════════════════════
# 3. 增删与状态
# ═══════════════════════════════════════════════════════════════

def test_build_creates_cart_objects_with_static_physics():
    sim = FakeSim()
    foreign = _seed_foreign_object(sim)
    result = cart_mod.build_cart(sim, cart_mod.CartSpec(x=3.5, y=2.3))

    assert result["ok"] is True
    assert result["object_count"] == 8
    assert result["replaced_previous"] is None
    root = result["root_handle"]
    assert sim.getObjectAlias(root) == cart_mod.CART_ROOT_ALIAS

    for alias in result["aliases"]:
        handle = next(h for h, o in sim.objects.items()
                      if o["alias"] == alias)
        if handle != root:
            assert sim.getObjectParent(handle) == root
            assert sim.getObjectInt32Param(
                handle, sim.shapeintparam_static) == 1
            assert sim.getObjectInt32Param(
                handle, sim.shapeintparam_respondable) == 1
            assert sim.getObjectColor(handle) is not None

    # 既有对象完全没被动过
    assert sim.getObjectAlias(foreign) == _FOREIGN_ALIAS
    assert sim.getObjectInt32Param(foreign, sim.shapeintparam_static) == 1


def test_build_refuses_while_simulation_running():
    sim = FakeSim()
    sim.state = sim.simulation_running
    with pytest.raises(cart_mod.CartStateError):
        cart_mod.build_cart(sim, cart_mod.CartSpec())


def test_build_refuses_second_cart_without_replace():
    sim = FakeSim()
    cart_mod.build_cart(sim, cart_mod.CartSpec())
    with pytest.raises(cart_mod.CartPlacementError):
        cart_mod.build_cart(sim, cart_mod.CartSpec())


def test_replace_rebuilds_single_cart():
    sim = FakeSim()
    cart_mod.build_cart(sim, cart_mod.CartSpec())
    result = cart_mod.build_cart(sim, cart_mod.CartSpec(), replace=True)
    assert result["replaced_previous"]["removed"] == 8
    assert len(cart_mod.cart_objects(sim)) == 8


def test_build_refuses_blocked_spot_unless_forced():
    sim = FakeSim()
    with pytest.raises(cart_mod.CartPlacementError):
        cart_mod.build_cart(sim, cart_mod.CartSpec(x=2.34, y=1.35))
    assert cart_mod.cart_objects(sim) == {}
    result = cart_mod.build_cart(sim, cart_mod.CartSpec(x=2.34, y=1.35),
                                 require_clear=False)
    assert result["clearance"]["ok"] is False


def test_remove_only_removes_cart():
    sim = FakeSim()
    foreign = _seed_foreign_object(sim)
    cart_mod.build_cart(sim, cart_mod.CartSpec())
    result = cart_mod.remove_cart(sim)
    assert result["ok"] is True and result["removed"] == 8
    assert cart_mod.cart_objects(sim) == {}
    assert sim.getObjectAlias(foreign) == _FOREIGN_ALIAS


def test_status_reports_pose_and_physics():
    sim = FakeSim()
    assert cart_mod.cart_status(sim)["present"] is False
    root = cart_mod.build_cart(sim, cart_mod.CartSpec(x=3.5, y=2.3))[
        "root_handle"]
    status = cart_mod.cart_status(sim)
    assert status["present"] is True
    assert status["object_count"] == 8
    pose = status["pose"]
    assert pose[0] == pytest.approx(3.5) and pose[1] == pytest.approx(2.3)
    assert sim.getObjectAlias(root) == cart_mod.CART_ROOT_ALIAS


# ═══════════════════════════════════════════════════════════════
# 4. 独立演示
# ═══════════════════════════════════════════════════════════════

def test_drive_steps_and_returns_to_start():
    sim = FakeSim()
    spec = cart_mod.CartSpec(x=3.5, y=2.3)
    cart_mod.build_cart(sim, spec)
    result = cart_mod.drive_cart(sim, spec, distance=0.3, speed=0.15)
    assert result["ok"] is True
    assert result["steps"] > 0
    assert sim.steps == result["steps"]
    assert sim.state == sim.simulation_stopped
    assert sim.stepping is False
    pose = result["pose"]
    assert pose[0] == pytest.approx(3.5)
    assert pose[1] == pytest.approx(2.3)


def test_drive_requires_existing_cart():
    sim = FakeSim()
    with pytest.raises(cart_mod.CartStateError):
        cart_mod.drive_cart(sim)


# ═══════════════════════════════════════════════════════════════
# 5. 边界: 不引入外层依赖, 不改配置
# ═══════════════════════════════════════════════════════════════

def test_cart_module_does_not_import_outer_layers():
    """源码级检查: cart.py 不导入 application / artifacts / persistence / cli."""
    source = pathlib.Path(cart_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    imported.add(node.module.split(".")[0])
            elif node.module:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
    assert not ({"application", "artifacts", "persistence", "cli"}
                & imported), imported


def test_cart_module_does_not_touch_frozen_config():
    from flexible_factory.simulation import config as cfg
    from flexible_factory.simulation import contract

    before = (contract.config_digest(), cfg.SCENARIO_BATCH["full_flow"])
    cart_mod.CartSpec(x=0.0, y=0.0)
    cart_mod.clearance_report()
    after = (contract.config_digest(), cfg.SCENARIO_BATCH["full_flow"])
    assert before == after
