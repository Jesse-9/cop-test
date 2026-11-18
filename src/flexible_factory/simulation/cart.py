"""cart.py — 车间手推小车（附加件，不参与调度）.

设计约束（对应仓库 CLAUDE.md 的项目不变量）:

  - **只新增对象**: 不改动任何现有对象、资源表、工艺目录、工件或 manifest;
    不修改 ``simulation/config*.py``，因此 ``config_digest`` 不变;
  - **不进入调度**: 不占资源、不建 ``Stage4Task``、不产生事件、不调用
    ``sim.step()``；步进只由调用方（``tools/cart.py drive``）自己负责;
  - **全部 static + respondable**: 不受重力推动、不会被 AGV 顶走，
    因此不会改变既有 ``full_flow`` 的物料流与验收结果;
  - **别名统一前缀** ``WorkshopCart``: 便于一键移除，也让 manifest 校验时
    的差异只落在 ``extra_aliases`` 一行里，不产生 ``missing``/``mismatch``。

默认停放位置 ``(3.50, 2.30)`` 的选取理由: 在 AGV 东直道 ``x=2.90`` 以东、
成品输出台（``y`` 不超过 1.45）以北、C→E 线与 E 岛碰撞包络之外。
与既有几何的最小间隙由 :func:`clearance_report` 计算并可在离线状态下验证
（``python tools/cart.py spot``，不需要启动 CoppeliaSim）。

本模块只在 ``sim`` 上调用以下 API:
``createDummy / createPrimitiveShape / setObjectAlias / setObjectParent /
setObjectPosition / setObjectOrientation / setObjectColor /
setObjectInt32Param / getObjectInt32Param / removeObjects /
getSimulationState``。不含任何仿真循环。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

from . import config_base as base
from . import physics as phys
from .layout import Stage4Layout, default_layout, rect_clearance
from .layout_base import Rect, rect_inside

CART_VERSION = "v1_cart_prop_20261116"
CART_PREFIX = "WorkshopCart"
CART_ROOT_ALIAS = CART_PREFIX

#: 默认停放点（世界坐标 XY, m）与朝向（rad）。
DEFAULT_SPOT = (3.50, 2.30)
DEFAULT_YAW = 0.0

#: 与既有几何要求的最小间隙（m）。低于该值视为压占。
CLEARANCE_GAP = 0.08

_COLOR_DECK = (0.32, 0.42, 0.60)
_COLOR_WHEEL = (0.10, 0.10, 0.11)
_COLOR_RAIL = (0.62, 0.64, 0.68)

#: 四个轮子的相对方位: (名称后缀, X 方向, Y 方向)
_WHEELS = (("FL", 1.0, 1.0), ("FR", 1.0, -1.0),
           ("RL", -1.0, 1.0), ("RR", -1.0, -1.0))


# ═══════════════════════════════════════════════════════════════
# 异常
# ═══════════════════════════════════════════════════════════════

class CartError(RuntimeError):
    code = "V1_CART"


class CartStateError(CartError):
    """仿真状态不允许该操作（运行中加/删物体）。"""

    code = "CART_STATE"


class CartPlacementError(CartError):
    """停放位置与既有几何冲突，或场景里已存在小车。"""

    code = "CART_PLACEMENT"


# ═══════════════════════════════════════════════════════════════
# 规格
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CartSpec:
    """小车的静态几何与停放位姿（世界坐标）。

    ``deck_*`` / ``wheel_*`` / ``rail_*`` 只是几何尺寸；改动它们不会影响
    任何既有逻辑，只改变这台附加车的外观与占地。
    """

    x: float = DEFAULT_SPOT[0]
    y: float = DEFAULT_SPOT[1]
    yaw: float = DEFAULT_YAW

    deck_size: tuple[float, float, float] = (0.42, 0.30, 0.04)
    deck_z: float = 0.135
    wheel_radius: float = 0.055
    wheel_width: float = 0.026
    wheel_dx: float = 0.145
    wheel_dy: float = 0.165
    rail_size: tuple[float, float, float] = (0.42, 0.018, 0.05)
    rail_z: float = 0.175

    # ── 派生几何 ────────────────────────────────────────────

    @property
    def half_extents(self) -> tuple[float, float]:
        """小车 XY 半尺寸（含轮子外凸），用于间隙计算。"""
        hx = max(self.deck_size[0] / 2.0,
                 self.wheel_dx + self.wheel_radius)
        hy = max(self.deck_size[1] / 2.0,
                 self.wheel_dy + self.wheel_width / 2.0)
        return (float(hx), float(hy))

    @property
    def footprint(self) -> Rect:
        """按 ``yaw`` 旋转后的 XY 轴对齐包围盒。"""
        hx, hy = self.half_extents
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        xs, ys = [], []
        for sx, sy in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
            lx, ly = sx * hx, sy * hy
            xs.append(self.x + c * lx - s * ly)
            ys.append(self.y + s * lx + c * ly)
        return (min(xs), min(ys), max(xs), max(ys))

    def moved(self, x: float, y: float, yaw: Optional[float] = None
              ) -> "CartSpec":
        return replace(self, x=float(x), y=float(y),
                       yaw=self.yaw if yaw is None else float(yaw))

    def aliases(self) -> tuple[str, ...]:
        return tuple(part["alias"] for part in cart_parts(self))

    def as_dict(self) -> dict:
        hx, hy = self.half_extents
        return {
            "version": CART_VERSION,
            "position": [round(self.x, 6), round(self.y, 6), 0.0],
            "yaw": round(self.yaw, 6),
            "deck_size": list(self.deck_size),
            "deck_z": self.deck_z,
            "wheel_radius": self.wheel_radius,
            "wheel_width": self.wheel_width,
            "wheel_dx": self.wheel_dx,
            "wheel_dy": self.wheel_dy,
            "half_extents": [round(hx, 6), round(hy, 6)],
            "footprint": [round(v, 6) for v in self.footprint],
        }


# ═══════════════════════════════════════════════════════════════
# 零件表（纯数据，便于离线测试）
# ═══════════════════════════════════════════════════════════════

def cart_parts(spec: Optional[CartSpec] = None) -> tuple[dict, ...]:
    """返回小车的零件描述（含根 dummy）。

    每项字段: ``alias`` / ``kind`` (``dummy``|``cuboid``|``cylinder``) /
    ``size`` / ``local_pos`` / ``orientation`` (相对根) / ``color``。
    """
    spec = spec or CartSpec()
    parts: list[dict] = [{
        "alias": CART_ROOT_ALIAS,
        "kind": "dummy",
        "size": (0.05, 0.05, 0.05),
        "local_pos": (0.0, 0.0, 0.0),
        "orientation": (0.0, 0.0, 0.0),
        "color": None,
    }]
    parts.append({
        "alias": f"{CART_PREFIX}_Deck",
        "kind": "cuboid",
        "size": tuple(spec.deck_size),
        "local_pos": (0.0, 0.0, spec.deck_z),
        "orientation": (0.0, 0.0, 0.0),
        "color": _COLOR_DECK,
    })
    for name, sx, sy in _WHEELS:
        parts.append({
            "alias": f"{CART_PREFIX}_Wheel_{name}",
            "kind": "cylinder",
            # 圆柱径向给直径, 轴向给厚度; 绕 X 转 90° 后轴向沿 Y (滚动轴)
            "size": (2.0 * spec.wheel_radius, 2.0 * spec.wheel_radius,
                     spec.wheel_width),
            "local_pos": (sx * spec.wheel_dx, sy * spec.wheel_dy,
                          spec.wheel_radius),
            "orientation": (math.pi / 2.0, 0.0, 0.0),
            "color": _COLOR_WHEEL,
        })
    for name, sy in (("L", 1.0), ("R", -1.0)):
        parts.append({
            "alias": f"{CART_PREFIX}_Rail_{name}",
            "kind": "cuboid",
            "size": tuple(spec.rail_size),
            "local_pos": (0.0, sy * (spec.deck_size[1] / 2.0), spec.rail_z),
            "orientation": (0.0, 0.0, 0.0),
            "color": _COLOR_RAIL,
        })
    return tuple(parts)


# ═══════════════════════════════════════════════════════════════
# 几何间隙（纯函数，不需要仿真器）
# ═══════════════════════════════════════════════════════════════

def obstacle_rects(layout: Optional[Stage4Layout] = None
                   ) -> tuple[tuple[str, Rect], ...]:
    """既有车间几何的 XY 投影（只读, 用于停放点间隙检查）。"""
    layout = layout or default_layout()
    items: list[tuple[str, Rect]] = [
        ("conveyor_in", layout.conveyor_rect),
        ("warehouse_s1", layout.warehouse_rect),
        ("warehouse_s2", layout.warehouse_s2_rect),
        ("e_output_table", layout.e_output_table_rect),
    ]
    for line_id, line in layout.lines.items():
        items.append((f"line:{line_id}", line.rect))
    for spec in layout.units:
        items.append((f"island:{spec.unit_id}", spec.island_rect))
        if spec.has_station:
            items.append((f"station:{spec.unit_id}", spec.station_rect))
        if spec.dock is not None:
            dock_rect = spec.dock_rect
            if dock_rect is not None:
                items.append((f"dock:{spec.unit_id}", dock_rect))
    for name, rect in layout.agv_lane_rects.items():
        items.append((f"agv_lane:{name}", rect))
    return tuple(items)


def clearance_report(spec: Optional[CartSpec] = None,
                     layout: Optional[Stage4Layout] = None,
                     *, gap: float = CLEARANCE_GAP) -> dict:
    """计算小车占地与既有几何的间隙；不连接仿真器。"""
    spec = spec or CartSpec()
    layout = layout or default_layout()
    rect = spec.footprint
    inside = rect_inside(rect, layout.floor_rect)
    gaps: list[tuple[float, str]] = []
    blocked: list[dict] = []
    for name, obstacle in obstacle_rects(layout):
        value = float(rect_clearance(rect, obstacle))
        gaps.append((value, name))
        if value < gap:
            blocked.append({"name": name, "gap": round(value, 4),
                            "rect": [round(v, 4) for v in obstacle]})
    nearest_gap, nearest = min(gaps) if gaps else (float("inf"), "")
    return {
        "ok": bool(inside and not blocked),
        "spec": spec.as_dict(),
        "footprint": [round(v, 6) for v in rect],
        "floor_rect": [round(v, 6) for v in layout.floor_rect],
        "inside_floor": bool(inside),
        "required_gap": float(gap),
        "nearest_obstacle": nearest,
        "nearest_gap": round(nearest_gap, 4) if gaps else None,
        "blocked_by": blocked,
    }


def default_cart_spec(layout: Optional[Stage4Layout] = None) -> CartSpec:
    """按候选点顺序返回第一个与既有几何不冲突的停放规格。"""
    layout = layout or default_layout()
    candidates = (
        DEFAULT_SPOT,
        (3.30, 2.45),
        (2.20, 2.45),
        (0.00, 2.45),
        (-2.40, 2.20),
        (3.50, 2.60),
        (-2.90, -0.60),
    )
    for x, y in candidates:
        spec = CartSpec(x=float(x), y=float(y), yaw=DEFAULT_YAW)
        if clearance_report(spec, layout)["ok"]:
            return spec
    raise CartPlacementError(
        "找不到与既有几何保持 %.2f m 间隙的停放点; "
        "请用 --x/--y 显式指定一个空位" % CLEARANCE_GAP)


# ═══════════════════════════════════════════════════════════════
# 场景操作（需要已连接的 sim）
# ═══════════════════════════════════════════════════════════════

def cart_objects(sim) -> dict[str, int]:
    """当前场景里所有属于小车的对象（alias → handle）。"""
    from .scene_base import all_objects, alias_of

    found: dict[str, int] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if not alias:
            continue
        if alias == CART_ROOT_ALIAS or alias.startswith(CART_PREFIX + "_"):
            found[alias] = int(handle)
    return found


def _require_stopped(sim) -> None:
    try:
        state = int(sim.getSimulationState())
    except Exception as exc:  # pragma: no cover - 连接异常
        raise CartStateError(f"无法读取仿真状态: {exc}") from exc
    if state != int(sim.simulation_stopped):
        raise CartStateError(
            "必须在仿真停止状态增删物体 (当前 state=%d); "
            "先执行 flexible-factory stop" % state)


def build_cart(sim, spec: Optional[CartSpec] = None, *,
               replace: bool = False,
               layout: Optional[Stage4Layout] = None,
               require_clear: bool = True) -> dict:
    """在当前已加载场景里创建小车；返回创建结果与间隙报告。

    不保存工件、不修改 manifest、不触碰任何既有对象。
    """
    layout = layout or default_layout()
    spec = spec or default_cart_spec(layout)
    _require_stopped(sim)

    existing = cart_objects(sim)
    if existing and not replace:
        raise CartPlacementError(
            "场景里已存在小车 (%d 个对象, 根=%s); 用 --replace 重建"
            % (len(existing), CART_ROOT_ALIAS))
    removed = None
    if existing:
        removed = remove_cart(sim)

    report = clearance_report(spec, layout)
    if require_clear and not report["ok"]:
        raise CartPlacementError(
            "停放点与既有几何冲突: "
            + ", ".join(item["name"] for item in report["blocked_by"]))

    parts = cart_parts(spec)
    root_info = parts[0]
    root = int(sim.createDummy(float(root_info["size"][0])))
    sim.setObjectAlias(root, CART_ROOT_ALIAS)
    sim.setObjectPosition(root, [spec.x, spec.y, 0.0], sim.handle_world)
    sim.setObjectOrientation(root, [0.0, 0.0, spec.yaw], sim.handle_world)

    handles: dict[str, int] = {CART_ROOT_ALIAS: root}
    for part in parts[1:]:
        handle = _create_part(sim, part)
        sim.setObjectParent(handle, root, True)
        sim.setObjectPosition(handle, list(part["local_pos"]), root)
        sim.setObjectOrientation(handle, list(part["orientation"]), root)
        sim.setObjectColor(handle, 0, sim.colorcomponent_ambient_diffuse,
                           list(part["color"]))
        handles[part["alias"]] = handle

    pose = _root_pose(sim)
    return {
        "ok": True,
        "version": CART_VERSION,
        "spec": spec.as_dict(),
        "aliases": sorted(handles),
        "object_count": len(handles),
        "root_handle": root,
        "pose": pose,
        "clearance": report,
        "replaced_previous": removed,
        "note": "附加件: 不占资源、不建任务、不进入 manifest; "
                "重新 load 场景后自动消失",
    }


def _create_part(sim, part: dict) -> int:
    kind = part["kind"]
    size = [float(v) for v in part["size"]]
    if kind == "cuboid":
        handle = int(sim.createPrimitiveShape(
            sim.primitiveshape_cuboid, size))
    elif kind == "cylinder":
        handle = int(sim.createPrimitiveShape(
            sim.primitiveshape_cylinder, size))
    else:  # pragma: no cover - 零件表是常量
        raise CartError(f"未知零件类型: {kind!r}")
    sim.setObjectAlias(handle, part["alias"])
    phys.apply_static_shape(sim, handle, respondable=True)
    return handle


def remove_cart(sim) -> dict:
    """删除全部小车对象；只按前缀匹配，不误删其它对象。"""
    _require_stopped(sim)
    found = cart_objects(sim)
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
    remaining = cart_objects(sim)
    return {
        "ok": not remaining,
        "removed": len(handles) - len(remaining),
        "aliases": sorted(found),
        "remaining": sorted(remaining),
    }


def cart_status(sim) -> dict:
    """只读查询：小车是否存在、根位姿、台面 shape 的物理标记。

    根节点是 dummy, 没有 shape 参数; ``static``/``respondable`` 以台面
    ``WorkshopCart_Deck`` 为准 (四个轮子与护栏同配置)。
    """
    found = cart_objects(sim)
    if not found:
        return {"present": False, "aliases": [], "version": CART_VERSION}
    deck = found.get(f"{CART_PREFIX}_Deck")
    physics = {}
    if deck is not None:
        physics = phys.snapshot_physics(sim, deck)
    return {
        "present": True,
        "version": CART_VERSION,
        "aliases": sorted(found),
        "object_count": len(found),
        "pose": _root_pose(sim),
        "physics_object": f"{CART_PREFIX}_Deck",
        "physics": physics,
    }


def _root_pose(sim) -> Optional[list[float]]:
    from .scene_base import resolve_alias

    try:
        handle = int(resolve_alias(sim, CART_ROOT_ALIAS, required=False))
    except Exception:
        return None
    if handle < 0:
        return None
    try:
        position = [round(float(v), 6)
                    for v in sim.getObjectPosition(handle, sim.handle_world)]
        orientation = [round(float(v), 6)
                       for v in sim.getObjectOrientation(handle,
                                                        sim.handle_world)]
        return position + orientation
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 演示：让小车沿 +X 直行再退回（独立会话，不参与 full_flow）
# ═══════════════════════════════════════════════════════════════

def drive_cart(sim, spec: Optional[CartSpec] = None, *,
               distance: float = 0.70,
               speed: float = 0.12,
               step: float = base.SIM_STEP) -> dict:
    """让小车沿自身 +X 前进 ``distance`` 再退回原位。

    这是**独立演示**：由本函数自己负责 stepping，协调器不参与；
    运行前必须处于停止态，结束后恢复停止态。轮子不旋转（视觉简化，
    避免在无真机验证时引入欧拉角错误）。
    """
    from .component_builder import start_stepping, stop_simulation

    if cart_objects(sim) == {}:
        raise CartStateError("场景里没有小车，先执行 add")
    if distance <= 0.0 or speed <= 0.0:
        raise CartError("distance / speed 必须为正数")

    spec = spec or CartSpec()
    steps = max(1, int(round(distance / max(speed * step, 1e-9))))
    start_stepping(sim)
    try:
        for index in range(steps):
            progress = (index + 1) / steps
            _place_root(sim, spec.x + distance * progress, spec.y, spec.yaw)
            sim.step()
        for index in range(steps):
            progress = (steps - 1 - index) / steps
            _place_root(sim, spec.x + distance * progress, spec.y, spec.yaw)
            sim.step()
    finally:
        stop_simulation(sim)

    return {
        "ok": True,
        "steps": steps * 2,
        "distance": float(distance),
        "speed": float(speed),
        "sim_time_estimate": round(steps * 2 * step, 3),
        "pose": _root_pose(sim),
        "note": "独立演示: 未使用协调器/资源表; 轮子不旋转",
    }


def _place_root(sim, x: float, y: float, yaw: float) -> None:
    from .scene_base import resolve_alias

    handle = resolve_alias(sim, CART_ROOT_ALIAS, required=True)
    sim.setObjectPosition(handle, [float(x), float(y), 0.0], sim.handle_world)
    sim.setObjectOrientation(handle, [0.0, 0.0, float(yaw)], sim.handle_world)
