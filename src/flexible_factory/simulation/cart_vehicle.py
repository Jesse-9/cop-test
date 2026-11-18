"""cart_vehicle.py — 小车二的物理车体与路线行驶（运动学，不参与调度）.

与 `cart.py` 的静态手推车不同, 这是一台**会沿路网行驶**的运输车:

  - 车体: 根 dummy + 台面 + 4 轮 + 载货托盘, 全部静态+可碰撞,
    靠父子变换跟随根节点 (已实测: 根平移 0.2 m, 台面/轮子同步 0.2 m);
  - 路线: 直接复用 `transport_schedule` 的 6 节点 / 3 直道, 不新增节点,
    因此 `layout_digest` 不变, v002 工件仍然可用;
  - 速度/转向/停留: 取 `transport_schedule.CART_2` (0.10 m/s, 0.5 s/次,
    装卸各 2.0 s), 与离线调度推演用的是同一套参数;
  - 行驶是**运动学**的: 每步 `setObjectPose` 直接给位姿, 不依赖轮速物理;
    因此只用于演示与后续接入, 不代表真实底盘动力学。

本模块不调用 `sim.step()` 之外的东西也在 stepping 会话里工作:
`drive_route` 只负责"走一步", `demo_route` 才负责 start/stop stepping。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import config_base as base
from . import physics as phys
from .transport_schedule import CART_2, NODES, plan_route

CART2_PREFIX = "Cart2"
CART2_ROOT = CART2_PREFIX
CART2_HOME = "D"

_COLOR_DECK = (0.28, 0.52, 0.42)
_COLOR_WHEEL = (0.10, 0.10, 0.11)
_COLOR_TRAY = (0.72, 0.58, 0.24)

_WHEELS = (("FL", 1.0, 1.0), ("FR", 1.0, -1.0),
           ("RL", -1.0, 1.0), ("RR", -1.0, -1.0))


class Cart2Error(RuntimeError):
    code = "V1_CART2"


class Cart2StateError(Cart2Error):
    code = "CART2_STATE"


@dataclass(frozen=True)
class Cart2Spec:
    """小车二的几何 (运动参数一律来自 transport_schedule.CART_2)。"""

    node: str = CART2_HOME
    deck_size: tuple[float, float, float] = (0.46, 0.34, 0.05)
    deck_z: float = 0.14
    wheel_radius: float = 0.05
    wheel_width: float = 0.024
    wheel_dx: float = 0.155
    wheel_dy: float = 0.185
    tray_size: tuple[float, float, float] = (0.26, 0.24, 0.03)
    tray_z: float = 0.18

    @property
    def position(self) -> tuple[float, float]:
        if self.node not in NODES:
            raise Cart2Error(f"未知节点: {self.node!r}")
        return NODES[self.node]

    def as_dict(self) -> dict:
        x, y = self.position
        return {"node": self.node, "position": [x, y, 0.0],
                "deck_size": list(self.deck_size),
                "speed": CART_2.speed,
                "turn_time": CART_2.turn_time,
                "load_time": CART_2.load_time,
                "unload_time": CART_2.unload_time}


def cart2_parts(spec: Optional[Cart2Spec] = None) -> tuple[dict, ...]:
    spec = spec or Cart2Spec()
    parts: list[dict] = [{
        "alias": CART2_ROOT, "kind": "dummy", "size": 0.05,
        "local_pos": (0.0, 0.0, 0.0), "orientation": (0.0, 0.0, 0.0),
        "color": None,
    }, {
        "alias": f"{CART2_PREFIX}_Deck", "kind": "cuboid",
        "size": tuple(spec.deck_size),
        "local_pos": (0.0, 0.0, spec.deck_z), "orientation": (0.0, 0.0, 0.0),
        "color": _COLOR_DECK,
    }, {
        "alias": f"{CART2_PREFIX}_Tray", "kind": "cuboid",
        "size": tuple(spec.tray_size),
        "local_pos": (0.0, 0.0, spec.tray_z), "orientation": (0.0, 0.0, 0.0),
        "color": _COLOR_TRAY,
    }]
    for name, sx, sy in _WHEELS:
        parts.append({
            "alias": f"{CART2_PREFIX}_Wheel_{name}", "kind": "cylinder",
            "size": (2.0 * spec.wheel_radius, 2.0 * spec.wheel_radius,
                     spec.wheel_width),
            "local_pos": (sx * spec.wheel_dx, sy * spec.wheel_dy,
                          spec.wheel_radius),
            "orientation": (math.pi / 2.0, 0.0, 0.0),
            "color": _COLOR_WHEEL,
        })
    return tuple(parts)


def cart2_objects(sim) -> dict[str, int]:
    from .scene_base import all_objects, alias_of

    found: dict[str, int] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if not alias:
            continue
        if alias == CART2_ROOT or alias.startswith(CART2_PREFIX + "_"):
            found[alias] = int(handle)
    return found


def _require_stopped(sim) -> None:
    if int(sim.getSimulationState()) != int(sim.simulation_stopped):
        raise Cart2StateError(
            "增删车体必须在仿真停止状态 (当前 state=%d)"
            % int(sim.getSimulationState()))


def build_cart2(sim, spec: Optional[Cart2Spec] = None, *,
                replace: bool = False) -> dict:
    from .scene_base import resolve_alias

    spec = spec or Cart2Spec()
    _require_stopped(sim)
    existing = cart2_objects(sim)
    if existing and not replace:
        raise Cart2StateError("场景里已有小车二; 用 replace=True 重建")
    removed = None
    if existing:
        removed = remove_cart2(sim)

    x, y = spec.position
    parts = cart2_parts(spec)
    root = int(sim.createDummy(float(parts[0]["size"])))
    sim.setObjectAlias(root, CART2_ROOT)
    sim.setObjectPosition(root, [x, y, 0.0], sim.handle_world)
    sim.setObjectOrientation(root, [0.0, 0.0, _initial_yaw(spec.node)],
                             sim.handle_world)
    handles = {CART2_ROOT: root}
    for part in parts[1:]:
        handle = int(sim.createPrimitiveShape(
            sim.primitiveshape_cuboid if part["kind"] == "cuboid"
            else sim.primitiveshape_cylinder, list(part["size"])))
        sim.setObjectAlias(handle, part["alias"])
        phys.apply_static_shape(sim, handle, respondable=True)
        sim.setObjectParent(handle, root, True)
        sim.setObjectPosition(handle, list(part["local_pos"]), root)
        sim.setObjectOrientation(handle, list(part["orientation"]), root)
        sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse,
                           list(part["color"]))
        handles[part["alias"]] = handle
    return {"ok": True, "node": spec.node, "aliases": sorted(handles),
            "object_count": len(handles), "root_handle": root,
            "pose": _root_pose(sim), "replaced_previous": removed,
            "spec": spec.as_dict(),
            "note": "运动学运输车: 位姿由 drive_route 直接驱动"}


def remove_cart2(sim) -> dict:
    _require_stopped(sim)
    found = cart2_objects(sim)
    if not found:
        return {"ok": True, "removed": 0, "aliases": []}
    handles = sorted(found.values(), reverse=True)
    try:
        sim.removeObjects(handles)
    except Exception:
        for handle in handles:
            try:
                sim.removeObjects([handle])
            except Exception:
                pass
    return {"ok": not cart2_objects(sim), "removed": len(handles),
            "aliases": sorted(found)}


def cart2_status(sim) -> dict:
    found = cart2_objects(sim)
    if not found:
        return {"present": False, "aliases": []}
    deck = found.get(f"{CART2_PREFIX}_Deck")
    return {"present": True, "aliases": sorted(found),
            "object_count": len(found), "pose": _root_pose(sim),
            "physics_object": f"{CART2_PREFIX}_Deck",
            "physics": ({} if deck is None
                        else phys.snapshot_physics(sim, deck))}


def _initial_yaw(node: str) -> float:
    """停车朝向: 东侧的两台朝 +Y (D→E), 其余朝 +X。"""
    return math.pi / 2.0 if node in ("D", "E") else 0.0


def _root_pose(sim) -> Optional[list[float]]:
    from .scene_base import resolve_alias

    handle = int(resolve_alias(sim, CART2_ROOT, required=False))
    if handle < 0:
        return None
    position = [round(float(v), 6)
                for v in sim.getObjectPosition(handle, sim.handle_world)]
    orientation = [round(float(v), 6)
                   for v in sim.getObjectOrientation(handle,
                                                     sim.handle_world)]
    return position + orientation


# ═══════════════════════════════════════════════════════════════
# 行驶
# ═══════════════════════════════════════════════════════════════

def drive_route(sim, route, *, speed: float = CART_2.speed,
                step: float = base.SIM_STEP,
                dwell: float = 0.0) -> dict:
    """沿一条 `Route` 逐段行驶 (调用方负责 stepping 会话)。

    每段: 原地转到段方向 → 匀速前进 → 到节点停下; 末段后可选停留。
    """
    from .scene_base import resolve_alias

    root = int(resolve_alias(sim, CART2_ROOT, required=True))
    if speed <= 0.0:
        raise Cart2Error("speed 必须为正数")

    x, y = NODES[route.nodes[0]]
    yaw = _initial_yaw(route.nodes[0])
    # 先把车放到起点 (重复 demo 时保证确定性)
    _place(sim, root, x, y, yaw)

    steps = 0
    for index in range(len(route.nodes) - 1):
        start = NODES[route.nodes[index]]
        end = NODES[route.nodes[index + 1]]
        target_yaw = math.atan2(end[1] - start[1], end[0] - start[0])
        turns = max(1, int(round(CART_2.turn_time / step)))
        for turn in range(turns):
            steps += 1
            _place(sim, root, start[0], start[1],
                   yaw + _wrap(target_yaw - yaw) * (turn + 1) / turns)
            sim.step()
        yaw = target_yaw
        distance = math.dist(start, end)
        segments = max(1, int(round(distance / (speed * step))))
        for segment in range(segments):
            steps += 1
            ratio = (segment + 1) / segments
            _place(sim, root, start[0] + (end[0] - start[0]) * ratio,
                   start[1] + (end[1] - start[1]) * ratio, yaw)
            sim.step()

    if dwell > 0.0:
        hold = max(0, int(round(dwell / step)))
        for _ in range(hold):
            steps += 1
            sim.step()

    return {"ok": True, "route": list(route.nodes),
            "length": round(route.length, 4), "steps": steps,
            "sim_time_s": round(steps * step, 3), "pose": _root_pose(sim)}


def _place(sim, root: int, x: float, y: float, yaw: float) -> None:
    sim.setObjectPosition(root, [float(x), float(y), 0.0], sim.handle_world)
    sim.setObjectOrientation(root, [0.0, 0.0, float(yaw)], sim.handle_world)


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _end_pose(route) -> tuple[float, float, float]:
    """路线终点的 (x, y, yaw); 末段方向即停车朝向。"""
    end = route.nodes[-1]
    x, y = NODES[end]
    if len(route.nodes) < 2:
        return x, y, _initial_yaw(end)
    previous = NODES[route.nodes[-2]]
    return x, y, math.atan2(y - previous[1], x - previous[0])


def demo_route(sim, origin: str = CART2_HOME, destination: str = "E") -> dict:
    """演示: 起点装货停留 → 行驶 → 终点卸货停留 (自己 start/stop stepping)。

    CoppeliaSim 在 stopSimulation 时把场景恢复到本次仿真起点, 所以停车后
    必须重新落位, 否则视觉上"车又回到了起点"。
    """
    from .component_builder import start_stepping, stop_simulation
    from .scene_base import resolve_alias

    if not cart2_objects(sim):
        raise Cart2StateError("场景里没有小车二, 先 build")
    route = plan_route(origin, destination)
    start_stepping(sim)
    try:
        loaded = drive_route(sim, plan_route(origin, origin),
                             dwell=CART_2.load_time)
        moved = drive_route(sim, route, dwell=CART_2.unload_time)
    finally:
        stop_simulation(sim)
    # 复位后补写终点位姿, 让场景停在"已送达"的状态
    root = int(resolve_alias(sim, CART2_ROOT, required=True))
    end_x, end_y, end_yaw = _end_pose(route)
    _place(sim, root, end_x, end_y, end_yaw)
    return {"ok": True, "origin": origin, "destination": destination,
            "route": list(route.nodes), "length": round(route.length, 4),
            "load_dwell_s": CART_2.load_time,
            "unload_dwell_s": CART_2.unload_time,
            "steps": loaded["steps"] + moved["steps"],
            "pose": _root_pose(sim),
            "note": "运动学演示: 不含轮速/碰撞动力学"}
