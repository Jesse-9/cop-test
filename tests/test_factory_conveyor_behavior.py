"""
tests/test_factory_conveyor_behavior.py
========================================
计划 §4.7 缺的 4 项单元测试:
  - 物料入口定位 (controller 调用 _spawn_from_warehouses 时,物理模式下落到 CONVEYORS[*]["start"])
  - 几何回退传送带 (capture_belt_pad_loop 返回 None, advance_belt_pad_loop 跳过, controller
    退回物料位置插值)
  - 背压冻结与恢复 (_conveyor_frozen 在岛拒绝时置 True, 岛可接受时清 False)
  - 工位出料触发机械臂动作 (B/D/A/C 岛 COMPLETE 时 _trigger_robot_action 被调用)

这些测试不依赖 CoppeliaSim (使用 Fake 模拟 sim / sim handle / island / agv),与
test_factory_physics.py / test_factory_robot.py 风格一致。
"""

import logging
from pathlib import Path
import sys

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import factory_config as cfg
from factory_types import ItemType, IslandState, Material
from factory_robot import RobotArmAnimator


# ═══════════════════════════════════════════════════════════════
# 公共 fake — 模拟 sim/工厂 controller 所需接口
# ═══════════════════════════════════════════════════════════════

class FakeSim:
    """最小 sim stub: 涵盖 controller 和 spawn_material 所需的所有调用."""
    object_shape_type = 0
    object_joint_type = 1
    object_dummy_type = 4
    handle_all = -2
    handle_world = -3
    handle_parent = -11
    primitiveshape_cuboid = 0
    colorcomponent_ambient_diffuse = 0

    def __init__(self):
        self.positions: dict[int, list[float]] = {}
        self.positions_list: list[tuple[int, list[float]]] = []
        self.next_handle = 1000
        # handles 1000+ 用于 spawn_material 创建; controller 注入的物料
        # 可在外部通过 record_position 注册到 positions 中

    def getObjectPosition(self, handle, _frame):
        return self.positions.get(int(handle), [0.0, 0.0, 0.0])

    def setObjectPosition(self, handle, pos, _frame):
        self.positions[int(handle)] = list(pos)
        self.positions_list.append((int(handle), list(pos)))

    def setObjectOrientation(self, handle, _euler, _frame):
        pass

    def createPrimitiveShape(self, _kind, _sizes):
        h = self.next_handle
        self.next_handle += 1
        return h

    def setObjectColor(self, *_a, **_kw):
        pass

    def getObjectsInTree(self, _root, _type, _opts):
        return []

    def getObjectAlias(self, _h):
        return ""

    def getObjectPose(self, _h, _frame):
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def interpolatePoses(self, p1, p2, t):
        return [a + (b - a) * t for a, b in zip(p1, p2)]

    def setObjectPose(self, handle, pose, _frame):
        self.positions[int(handle)] = list(pose[:3])


class _StubAGV:
    """最小 AGVScheduler stub,只用于 _update_robot_animations."""
    state = type("S", (), {"name": "IDLE"})()

    def current_position(self):
        return (0.0, 0.0)


def _build_controller(physics_mode: bool, fill_loops: bool = False):
    """构造一个轻量级 controller stub,只装填运行 _spawn_from_warehouses /
    _move_conveyors / _schedule_agv_tasks / _handle_ac_transfer /
    _handle_ce_transfer 所需的字段,不进入 run().
    """
    from factory_controller import FactoryController

    sim = FakeSim()
    # 给每条传送带填 handle,避免 _move_conveyors 跳过
    for cname, ccfg in cfg.CONVEYORS.items():
        ccfg["handle"] = sim.next_handle
        sim.next_handle += 1
    logger = logging.getLogger("ctrl-test")
    fc = FactoryController(sim, total_time=0.0, logger=logger)
    # 改 PHYSICS_CONVEYOR 为测试目标 (会改 cfg 模块全局)
    cfg.PHYSICS_CONVEYOR = physics_mode
    # 几何回退 (fill_loops=False): pad loop 全为 None → 走物料位置插值
    # 否则按 None 看待,advance_belt_pad_loop 不会被调 (因为 ccfg["handle"]>=0 但 loop=None)
    if not fill_loops:
        fc._conveyor_pad_loops = {cname: None for cname in cfg.CONVEYORS}
    return fc, sim


# ═══════════════════════════════════════════════════════════════
# 测试 1: 物料入口定位 — 物理模式下落到 CONVEYORS[*]["start"]
# ═══════════════════════════════════════════════════════════════

def test_physics_spawn_uses_conveyor_start_not_warehouse():
    """物理模式下,S1→A/S1→B/S2→D 的物料应生成在各自带面 start 入口,
    而不是仓库中心 (仓库中心远离带面,物料可能落到带外)."""
    fc, sim = _build_controller(physics_mode=True, fill_loops=False)
    fc.s1_timer_A = fc.s1_interval_A  # 立即触发 S1→A
    fc.s1_timer_B = fc.s1_interval_B  # 立即触发 S1→B
    fc.s2_timer_D = fc.s2_interval_D  # 立即触发 S2→D

    fc._spawn_from_warehouses(0.0)
    fc._spawn_from_warehouses(0.0)
    fc._spawn_from_warehouses(0.0)

    # 应生成 3 个物料,落点 X/Y 与各自带面 start 一致
    assert len(fc.materials) == 3, f"应生成 3 个物料, 实际 {len(fc.materials)}"
    by_conv = {m.on_conveyor: m for m in fc.materials}
    for cname, mat in by_conv.items():
        start = cfg.CONVEYORS[cname]["start"]
        assert mat.pos[0] == pytest.approx(start[0]), f"{cname}: X 应等于 {start[0]}"
        assert mat.pos[1] == pytest.approx(start[1]), f"{cname}: Y 应等于 {start[1]}"
        # 物理模式下 Z 应略高于带面,使其下落接触
        assert mat.pos[2] > cfg.CONVEYOR_SURFACE_Z, f"{cname}: Z 应高于带面 ({mat.pos[2]})"
        # 仓库中心 (-7.0, ±2.0/±4.5) 不应出现
        assert mat.pos[0] > cfg.WAREHOUSES["S1"]["pos"][0], \
            f"{cname}: X 不应落回仓库中心"


# ═══════════════════════════════════════════════════════════════
# 测试 2: 几何回退传送带 — capture_belt_pad_loop=None 时物料按 conv_progress 插值
# ═══════════════════════════════════════════════════════════════

def test_geometry_fallback_moves_material_by_conv_progress():
    """几何回退传送带 (pad loop=None) 仍按 conv_progress 推进物料位置。
    验证 _move_conveyors_physics 在 loop=None 时, setObjectPosition 被调用,
    且位置从 start 渐进到 end 方向。
    """
    fc, sim = _build_controller(physics_mode=True, fill_loops=False)
    # 注入一个物料到 S1_to_A 带面,起点 progress=0
    mat = Material(uid=42, item_type=ItemType.RAW_A, handle=sim.next_handle)
    sim.next_handle += 1
    mat.on_conveyor = "S1_to_A"
    mat.conv_progress = 0.0
    mat.pos = list(cfg.CONVEYORS["S1_to_A"]["start"]) + [cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_SPAWN_GAP]
    sim.positions[mat.handle] = list(mat.pos)
    fc.materials.append(mat)

    # 推 1 步 (dt = 0.05s, CONVEYOR_SPEED=0.5 m/s, 长度 ≈ 3.2 m → delta ≈ 0.0078)
    fc._move_conveyors(0.05)

    assert len(sim.positions_list) >= 1, "回退路径应调用 setObjectPosition"
    moved_to = sim.positions_list[-1][1]
    start = cfg.CONVEYORS["S1_to_A"]["start"]
    end = cfg.CONVEYORS["S1_to_A"]["end"]
    # X 应在 start[0] 和 end[0] 之间 (即沿 +X 推进)
    assert start[0] <= moved_to[0] <= end[0] + 1e-6, \
        f"X={moved_to[0]} 不在 [{start[0]}, {end[0]}] 范围内"
    assert mat.conv_progress > 0.0, f"conv_progress 应已推进, 实际 {mat.conv_progress}"


# ═══════════════════════════════════════════════════════════════
# 测试 3: 背压冻结与恢复 — 岛拒绝时 _conveyor_frozen 置 True
# ═══════════════════════════════════════════════════════════════

def test_backpressure_freeze_and_unfreeze():
    """物料到达带末端但岛拒绝 (can_accept=False) → _conveyor_frozen[cname]=True;
    岛恢复接受 → _conveyor_frozen[cname]=False.
    """
    fc, sim = _build_controller(physics_mode=True, fill_loops=False)
    # 强制 A 岛进入 TIMEOUT (拒绝所有来料)
    fc.islands["A"].state = IslandState.TIMEOUT
    # 注入一个物料,直接放到带末端 (即 end 坐标),让距离 ≤ ARRIVAL_RADIUS
    end_x, end_y = cfg.CONVEYORS["S1_to_A"]["end"]
    mat = Material(uid=99, item_type=ItemType.RAW_A,
                   handle=sim.next_handle,
                   pos=[end_x, end_y, cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_SPAWN_GAP])
    sim.next_handle += 1
    mat.on_conveyor = "S1_to_A"
    mat.conv_progress = 1.0
    sim.positions[mat.handle] = list(mat.pos)
    fc.materials.append(mat)

    # 第一次 _move_conveyors: 岛拒绝 → should_freeze=True, mat.frozen=True
    fc._move_conveyors(0.05)
    assert fc._conveyor_frozen["S1_to_A"] is True, \
        f"岛拒绝时传送带应冻结, 实际 {fc._conveyor_frozen['S1_to_A']}"
    assert mat.frozen is True, "物料应标记为 frozen"
    assert mat.on_conveyor == "S1_to_A", "物料仍应在带面"

    # 解除 A 岛超时
    fc.islands["A"].state = IslandState.IDLE
    fc.islands["A"].timeout_notified = False
    fc.islands["A"].queue = []  # 队列空可入岛

    # 第二次 _move_conveyors: 岛可接受 → 解冻 + 入岛
    fc._move_conveyors(0.05)
    assert fc._conveyor_frozen["S1_to_A"] is False, \
        f"岛可接受后传送带应解冻, 实际 {fc._conveyor_frozen['S1_to_A']}"
    assert mat.at_island == "A", f"物料应入岛 A, 实际 {mat.at_island}"


# ═══════════════════════════════════════════════════════════════
# 测试 4: 工位出料触发机械臂动作
# ═══════════════════════════════════════════════════════════════

def test_island_completion_triggers_robot_pick_and_place():
    """B/D 岛 COMPLETE 时, _schedule_agv_tasks 必须调用 _trigger_robot_action
    触发对应机械臂的 pick_and_place 动画; A/C 岛 COMPLETE 在 _handle_ac_transfer /
    _handle_ce_transfer 中触发。
    """
    fc, sim = _build_controller(physics_mode=True, fill_loops=False)
    # 用一个最小 robot_animator stub 记录 trigger 调用
    triggers: list[tuple[str, str]] = []

    class _StubAnimator:
        def trigger(self, island_key, anim_name):
            triggers.append((island_key, anim_name))

        def update(self, dt, islands, agv):
            pass

        def on_assembly(self):
            pass

    fc.robot_animator = _StubAnimator()

    # AGV stub: request_task 不会被实际触发业务,但需要存在
    class _AGV:
        state = type("S", (), {"name": "IDLE"})()
        def request_task(self, *a, **kw): pass
        def current_position(self): return (0.0, 0.0)
    fc.agv_scheduler = _AGV()

    # B 岛 COMPLETE: controller 找到物料并触发机器人
    fc.islands["B"].state = IslandState.COMPLETE
    fc.islands["B"].current_material_uid = 11
    fc.materials.append(Material(uid=11, item_type=ItemType.RAW_B,
                                  handle=2000))
    fc._schedule_agv_tasks(0.05)
    assert ("B", "pick_and_place") in triggers, \
        f"B 岛完成应触发 pick_and_place, 实际: {triggers}"

    # D 岛 COMPLETE
    fc.islands["D"].state = IslandState.COMPLETE
    fc.islands["D"].current_material_uid = 22
    fc.materials.append(Material(uid=22, item_type=ItemType.RAW_D,
                                  handle=2001))
    fc._schedule_agv_tasks(0.05)
    assert ("D", "pick_and_place") in triggers, \
        f"D 岛完成应触发 pick_and_place, 实际: {triggers}"

    # A 岛 COMPLETE → _handle_ac_transfer 触发 pick_and_place
    fc.islands["A"].state = IslandState.COMPLETE
    fc.islands["A"].current_material_uid = 33
    fc.materials.append(Material(uid=33, item_type=ItemType.RAW_A,
                                  handle=2002))
    fc._handle_ac_transfer(0.05)
    assert ("A", "pick_and_place") in triggers, \
        f"A 岛完成应触发 pick_and_place, 实际: {triggers}"

    # C 岛 COMPLETE → _handle_ce_transfer 触发 pick_and_place
    fc.islands["C"].state = IslandState.COMPLETE
    fc.islands["C"].current_material_uid = 44
    fc.materials.append(Material(uid=44, item_type=ItemType.RAW_A,
                                  handle=2003))
    fc._handle_ce_transfer(0.05)
    assert ("C", "pick_and_place") in triggers, \
        f"C 岛完成应触发 pick_and_place, 实际: {triggers}"


# ═══════════════════════════════════════════════════════════════
# 测试 5: idle_wiggle 不会覆盖 pick_and_place
# ═══════════════════════════════════════════════════════════════

def test_idle_wiggle_does_not_interrupt_pick_and_place(monkeypatch):
    """机械臂处于 pick_and_place 动画期间, update() 自动触发的 idle_wiggle
    不应覆盖 (因为 trigger() 只在 anim is None 时才置 idle_wiggle)。
    """
    monkeypatch.setattr(cfg, "ISLAND_ROBOTS", {
        "A": {"alias": "Test_Robot_A"},
    })

    class _Sim:
        object_shape_type = 0
        object_joint_type = 1
        object_dummy_type = 4
        handle_all = -2
        def getObjects(self, i, t):
            return 100 if i == 0 and t == 0 else -1
        def getObjectAlias(self, h):
            return "Test_Robot_A" if h == 100 else "joint"
        def getObjectsInTree(self, root, t, opt): return [root, 101, 102, 103, 104, 105, 106]
        def getObjectType(self, h): return 0 if h == 100 else 1
        def getObjectParent(self, h): return {101: 100, 102: 101, 103: 102,
                                              104: 103, 105: 104, 106: 105}.get(h, -1)
        def setJointTargetPosition(self, jh, a): pass

    animator = RobotArmAnimator(_Sim(), logging.getLogger("test"))
    # 显式触发 pick_and_place
    animator.trigger("A", "pick_and_place")
    assert animator.arms["A"]["anim"] == "pick_and_place"

    # 模拟岛进入 PROCESSING — controller 调 update() 试图触发 idle_wiggle
    class _Island:
        state = type("S", (), {"name": "PROCESSING"})()
    animator.update(0.05, {"A": _Island()}, _StubAGV())

    # pick_and_place 期间, anim 仍应是 pick_and_place (idle_wiggle 没覆盖)
    assert animator.arms["A"]["anim"] == "pick_and_place", \
        f"pick_and_place 期间不应被 idle_wiggle 覆盖, 实际 {animator.arms['A']['anim']}"


# ═══════════════════════════════════════════════════════════════
# 测试 6: 几何回退带物料完整从 progress=0.9 推进到入岛
# ═══════════════════════════════════════════════════════════════

def test_geometry_fallback_advances_material_all_the_way_to_island():
    """_conveyor_pad_loops[cname]=None 时, 物料按 conv_progress 推进
    直至入岛 (at_island 设为目标岛)。
    """
    fc, sim = _build_controller(physics_mode=True, fill_loops=False)
    mat = Material(uid=1, item_type=ItemType.RAW_A, handle=sim.next_handle)
    sim.next_handle += 1
    mat.on_conveyor = "S1_to_A"
    mat.conv_progress = 0.9
    mat.pos = list(cfg.CONVEYORS["S1_to_A"]["end"]) + [cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_SPAWN_GAP]
    sim.positions[mat.handle] = list(mat.pos)
    fc.materials.append(mat)

    # 推 200 步 (10s 仿真时间)
    for _ in range(200):
        fc._move_conveyors(0.05)
        if mat.at_island is not None or mat.on_conveyor is None:
            break

    assert mat.at_island == "A", f"应入岛 A, 实际 {mat.at_island}"
    assert fc._conveyor_pad_loops["S1_to_A"] is None, "回退路径下 loop 应仍为 None"
