"""layout_dual.py — v1 双站层布局、派生目标与静态几何校验 (L2_MIRROR).

`Stage3Layout` 是不可变布局描述: 两个工位、共享传送带取料区、两个 AGV
dock 和一条直线转运路线. 场景构建/控制器只读取该对象, 不在控制循环内
硬编码坐标.

`UnitLayoutView` 是单个工位的适配器, 供 v1 运动层 `Stage2UnitController`
使用: 它只暴露该工位的 `arm_base`、station/AGV 目标位姿与支撑高度,
不共享另一工位的可变状态.

布局选择: 06-12 §3.2 要求两个候选做有限筛选. 本模块提供:
  - `L2_MIRROR` — 传送带两侧镜像 (生产默认);
  - `L3_SAME_SIDE` — 同侧沿 X 错位 (仅候选, 静态检查即拒绝).
`screen_candidates()` 只做静态几何/可达性筛选; 真实 simIK 与碰撞查询由
v1 启动点校验在仿真器在线时执行, 不能把静态筛选当成完整碰撞证明.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import config_base as base
from .layout_base import (Rect, overlap_rects, rect_inside, rect_of)

from . import config_dual as cfg


# ═══════════════════════════════════════════════════════════════
# 工位描述
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UnitSpec:
    """一个 UR5 工位的世界坐标与派生目标.

    A 单元沿用 L1_S1 的机械臂/岛体/置物台坐标; B 单元关于 Y=0 镜像.
    `dock` 是该工位装卸 AGV 的停靠中心.
    """

    unit_id: str
    index: int
    arm_base: tuple[float, float, float]
    island_pos: tuple[float, float]
    station_pos: tuple[float, float]
    dock: tuple[float, float]
    island_size: tuple[float, float, float] = tuple(base.ISLAND_SIZE)
    station_size: tuple[float, float, float] = tuple(base.STATION_SIZE)
    island_top_z: float = base.ISLAND_TOP_Z
    station_top_z: float = base.STATION_TOP_Z

    # ── 别名 ────────────────────────────────────────────────

    @property
    def aliases(self) -> dict[str, str]:
        return dict(cfg.ALIASES["unit"][self.unit_id])

    @property
    def dock_sensor_alias(self) -> str:
        return cfg.ALIASES["dock_sensor"][self.unit_id]

    # ── 平面投影 ────────────────────────────────────────────

    @property
    def island_rect(self) -> Rect:
        return rect_of(self.island_pos, self.island_size[:2])

    @property
    def station_rect(self) -> Rect:
        return rect_of(self.station_pos, self.station_size[:2])

    @property
    def dock_rect(self) -> Rect:
        """AGV 在 dock 处朝向 -Y 时的车体占地."""
        return rect_of(self.dock, cfg.AGV_FOOTPRINT_AT_DOCK)

    # ── 目标位姿 ────────────────────────────────────────────

    @property
    def pick_material_pose(self) -> tuple[float, float, float]:
        return tuple(cfg.PICK_ZONE_CENTER)

    @property
    def pick_tcp_pose(self) -> list[float]:
        return base.tcp_pose_above(self.pick_material_pose)

    @property
    def station_material_pose(self) -> tuple[float, float, float]:
        return (self.station_pos[0], self.station_pos[1],
                self.station_top_z + base.MATERIAL_HALF_HEIGHT)

    @property
    def station_tcp_pose(self) -> list[float]:
        return base.tcp_pose_above(self.station_material_pose)

    @property
    def agv_material_pose(self) -> tuple[float, float, float]:
        return (self.dock[0], self.dock[1],
                base.AGV_TRAY_TOP_Z + base.MATERIAL_HALF_HEIGHT)

    @property
    def agv_tcp_pose(self) -> list[float]:
        return base.tcp_pose_above(self.agv_material_pose)

    def tcp_pose_for_target(self, target: str) -> list[float]:
        if target == "station":
            return list(self.station_tcp_pose)
        if target == "agv":
            return list(self.agv_tcp_pose)
        if target == "pick":
            return list(self.pick_tcp_pose)
        raise ValueError(f"未知目标: {target!r} (可用: pick/station/agv)")

    def material_pose_for_target(self, target: str) -> list[float]:
        if target == "station":
            pose = self.station_material_pose
        elif target == "agv":
            pose = self.agv_material_pose
        elif target == "pick":
            pose = self.pick_material_pose
        else:
            raise ValueError(f"未知目标: {target!r} (可用: pick/station/agv)")
        return [*pose, 0.0, 0.0, 0.0, 1.0]

    def targets(self) -> dict[str, list[float]]:
        return {
            "pick": list(self.pick_tcp_pose),
            "station": list(self.station_tcp_pose),
            "agv": list(self.agv_tcp_pose),
        }

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "index": self.index,
            "arm_base": list(self.arm_base),
            "island_pos": list(self.island_pos),
            "station_pos": list(self.station_pos),
            "dock": list(self.dock),
            "island_size": list(self.island_size),
            "station_size": list(self.station_size),
            "island_top_z": self.island_top_z,
            "station_top_z": self.station_top_z,
            "aliases": self.aliases,
            "dock_sensor_alias": self.dock_sensor_alias,
        }


# ═══════════════════════════════════════════════════════════════
# 单工位适配器 (供阶段二控制器复用)
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UnitLayoutView:
    """把 `UnitSpec` 适配成阶段一/二控制器期望的 layout 接口.

    控制器只读取 `arm_base`、`station_top_z`、`agv_dock` 和目标位姿;
    这里不复制阶段一全局常量, 也不暴露另一工位的状态.
    """

    layout_name: str
    spec: UnitSpec

    @property
    def name(self) -> str:
        return f"{self.layout_name}/{self.spec.unit_id}"

    @property
    def unit_id(self) -> str:
        return self.spec.unit_id

    @property
    def arm_base(self) -> tuple[float, float, float]:
        return self.spec.arm_base

    @property
    def island_pos(self) -> tuple[float, float]:
        return self.spec.island_pos

    @property
    def station_pos(self) -> tuple[float, float]:
        return self.spec.station_pos

    @property
    def agv_dock(self) -> tuple[float, float]:
        return self.spec.dock

    @property
    def island_top_z(self) -> float:
        return self.spec.island_top_z

    @property
    def station_top_z(self) -> float:
        return self.spec.station_top_z

    @property
    def pick_material_pose(self):
        return self.spec.pick_material_pose

    @property
    def pick_tcp_pose(self):
        return self.spec.pick_tcp_pose

    @property
    def station_material_pose(self):
        return self.spec.station_material_pose

    @property
    def station_tcp_pose(self):
        return self.spec.station_tcp_pose

    @property
    def agv_material_pose(self):
        return self.spec.agv_material_pose

    @property
    def agv_tcp_pose(self):
        return self.spec.agv_tcp_pose

    def tcp_pose_for_target(self, target: str) -> list[float]:
        return self.spec.tcp_pose_for_target(target)

    def material_pose_for_target(self, target: str) -> list[float]:
        return self.spec.material_pose_for_target(target)

    def targets(self) -> dict[str, list[float]]:
        return self.spec.targets()

    @property
    def island_rect(self) -> Rect:
        return self.spec.island_rect

    @property
    def station_rect(self) -> Rect:
        return self.spec.station_rect

    @property
    def agv_rect(self) -> Rect:
        return self.spec.dock_rect

    @property
    def floor_rect(self) -> Rect:
        return rect_of(base.FLOOR_CENTER[:2], base.FLOOR_SIZE[:2])

    @property
    def conveyor_rect(self) -> Rect:
        return rect_of(base.CONVEYOR_CENTER,
                       (base.CONVEYOR_DESIGN_LENGTH,
                        base.CONVEYOR_NATIVE_WIDTH))

    @property
    def warehouse_rect(self) -> Rect:
        return rect_of(base.WAREHOUSE_POS, base.WAREHOUSE_SIZE[:2])


# ═══════════════════════════════════════════════════════════════
# 双工位布局
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Stage3Layout:
    """两个工位、共享取料区、两个 dock 与 AGV 直线路线."""

    name: str
    units: tuple[UnitSpec, UnitSpec]
    conveyor_center: tuple[float, float] = tuple(base.CONVEYOR_CENTER)
    warehouse_pos: tuple[float, float] = tuple(base.WAREHOUSE_POS)
    pick_zone_center: tuple[float, float, float] = tuple(cfg.PICK_ZONE_CENTER)
    agv_initial_yaw: float = cfg.AGV_INITIAL_YAW
    agv_route_x: float = cfg.AGV_ROUTE_X
    agv_route_y: tuple[float, float] = tuple(cfg.AGV_ROUTE_Y)

    # ── 工位访问 ────────────────────────────────────────────

    def unit(self, unit_id: str) -> UnitSpec:
        for spec in self.units:
            if spec.unit_id == unit_id:
                return spec
        raise KeyError(f"未知工位: {unit_id!r} (可用: "
                       f"{[u.unit_id for u in self.units]})")

    def unit_by_index(self, index: int) -> UnitSpec:
        for spec in self.units:
            if spec.index == int(index):
                return spec
        raise KeyError(f"未知工位编号: {index}")

    def unit_view(self, unit_id: str) -> UnitLayoutView:
        return UnitLayoutView(layout_name=self.name, spec=self.unit(unit_id))

    @property
    def unit_ids(self) -> tuple[str, str]:
        return tuple(spec.unit_id for spec in self.units)  # type: ignore[return-value]

    @property
    def docks(self) -> dict[str, tuple[float, float]]:
        return {spec.unit_id: spec.dock for spec in self.units}

    @property
    def dock_sensor_aliases(self) -> dict[str, str]:
        return {spec.unit_id: spec.dock_sensor_alias for spec in self.units}

    # ── 兼容阶段一 LayoutSpec 的只读接口 (仅 A 单元) ─────────

    @property
    def arm_base(self) -> tuple[float, float, float]:
        return self.unit("A").arm_base

    @property
    def island_pos(self) -> tuple[float, float]:
        return self.unit("A").island_pos

    @property
    def station_pos(self) -> tuple[float, float]:
        return self.unit("A").station_pos

    @property
    def agv_dock(self) -> tuple[float, float]:
        return self.unit("A").dock

    @property
    def island_top_z(self) -> float:
        return self.unit("A").island_top_z

    @property
    def station_top_z(self) -> float:
        return self.unit("A").station_top_z

    @property
    def island_size(self):
        return self.unit("A").island_size

    @property
    def station_size(self):
        return self.unit("A").station_size

    @property
    def pick_sensor_local_x(self) -> float:
        return base.CONVEYOR_STOP_LOCAL_X

    @property
    def pick_material_pose(self):
        return self.unit("A").pick_material_pose

    @property
    def pick_tcp_pose(self):
        return self.unit("A").pick_tcp_pose

    @property
    def station_material_pose(self):
        return self.unit("A").station_material_pose

    @property
    def station_tcp_pose(self):
        return self.unit("A").station_tcp_pose

    @property
    def agv_material_pose(self):
        return self.unit("A").agv_material_pose

    @property
    def agv_tcp_pose(self):
        return self.unit("A").agv_tcp_pose

    def tcp_pose_for_target(self, target: str) -> list[float]:
        return self.unit("A").tcp_pose_for_target(target)

    def material_pose_for_target(self, target: str) -> list[float]:
        return self.unit("A").material_pose_for_target(target)

    def targets(self) -> dict[str, list[float]]:
        return self.unit("A").targets()

    @property
    def conveyor_rect(self) -> Rect:
        return rect_of(self.conveyor_center,
                       (base.CONVEYOR_DESIGN_LENGTH,
                        base.CONVEYOR_NATIVE_WIDTH))

    @property
    def warehouse_rect(self) -> Rect:
        return rect_of(self.warehouse_pos, base.WAREHOUSE_SIZE[:2])

    @property
    def floor_rect(self) -> Rect:
        return rect_of(base.FLOOR_CENTER[:2], base.FLOOR_SIZE[:2])

    @property
    def island_rect(self) -> Rect:
        return self.unit("A").island_rect

    @property
    def station_rect(self) -> Rect:
        return self.unit("A").station_rect

    @property
    def agv_rect(self) -> Rect:
        return self.unit("A").dock_rect

    # ── AGV 路线/通道 ───────────────────────────────────────

    @property
    def agv_route_rect(self) -> Rect:
        """AGV 在两个 dock 之间行驶的车体扫掠矩形 (含停靠误差)."""
        half_x = (cfg.AGV_ROUTE_HALF_WIDTH + cfg.AGV_ROUTE_CLEARANCE)
        half_y = (cfg.AGV_FOOTPRINT_AT_DOCK[1] / 2.0
                  + cfg.AGV_ROUTE_ARRIVE_TOL + cfg.AGV_ROUTE_CLEARANCE)
        y0 = min(spec.dock[1] for spec in self.units)
        y1 = max(spec.dock[1] for spec in self.units)
        return (self.agv_route_x - half_x, y0 - half_y,
                self.agv_route_x + half_x, y1 + half_y)

    @property
    def agv_route_pose_a(self) -> list[float]:
        a = self.unit("A")
        return [a.dock[0], a.dock[1], base.AGV_ROOT_Z,
                *quat_from_yaw(self.agv_initial_yaw)]

    @property
    def agv_route_pose_b(self) -> list[float]:
        b = self.unit("B")
        return [b.dock[0], b.dock[1], base.AGV_ROOT_Z,
                *quat_from_yaw(self.agv_initial_yaw)]

    # ── 序列化 ──────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "units": [spec.as_dict() for spec in self.units],
            "conveyor_center": list(self.conveyor_center),
            "warehouse_pos": list(self.warehouse_pos),
            "pick_zone_center": list(self.pick_zone_center),
            "agv_initial_yaw": self.agv_initial_yaw,
            "agv_route_x": self.agv_route_x,
            "agv_route_y": list(self.agv_route_y),
        }

    def digest_payload(self) -> dict:
        """稳定布局摘要 (不含派生 Rect/浮点重排)."""
        return self.as_dict()


# ═══════════════════════════════════════════════════════════════
# 四元数 / 候选布局
# ═══════════════════════════════════════════════════════════════

def quat_from_yaw(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(float(yaw) / 2.0),
            math.cos(float(yaw) / 2.0))


def yaw_from_quat(q) -> float:
    x, y, z, w = [float(v) for v in q[:4]]
    return math.atan2(2.0 * (w * z + x * y),
                      1.0 - 2.0 * (y * y + z * z))


def _unit_from_dict(data: dict) -> UnitSpec:
    return UnitSpec(
        unit_id=str(data["unit_id"]),
        index=int(data.get("index", 0)),
        arm_base=tuple(float(v) for v in data["arm_base"]),
        island_pos=tuple(float(v) for v in data["island_pos"]),
        station_pos=tuple(float(v) for v in data["station_pos"]),
        dock=tuple(float(v) for v in data["dock"]),
        island_size=tuple(float(v) for v in data.get(
            "island_size", base.ISLAND_SIZE)),
        station_size=tuple(float(v) for v in data.get(
            "station_size", base.STATION_SIZE)),
        island_top_z=float(data.get("island_top_z", base.ISLAND_TOP_Z)),
        station_top_z=float(data.get("station_top_z", base.STATION_TOP_Z)),
    )


def layout_from_dict(data: dict) -> Stage3Layout:
    """从 manifest/JSON 恢复布局; 缺失字段使用当前默认值."""
    if not isinstance(data, dict):
        raise ValueError("stage3 layout 必须是对象")
    units = tuple(_unit_from_dict(item) for item in data.get("units") or ())
    if len(units) != 2:
        raise ValueError(f"stage3 layout 必须包含 2 个工位, 实际 {len(units)}")
    name = str(data.get("name") or data.get("id") or "")
    if not name:
        raise ValueError("stage3 layout 缺少 name/id")
    return Stage3Layout(
        name=name,
        units=units,  # type: ignore[arg-type]
        conveyor_center=tuple(float(v) for v in data.get(
            "conveyor_center", base.CONVEYOR_CENTER)),
        warehouse_pos=tuple(float(v) for v in data.get(
            "warehouse_pos", base.WAREHOUSE_POS)),
        pick_zone_center=tuple(float(v) for v in data.get(
            "pick_zone_center", cfg.PICK_ZONE_CENTER)),
        agv_initial_yaw=float(data.get("agv_initial_yaw",
                                       cfg.AGV_INITIAL_YAW)),
        agv_route_x=float(data.get("agv_route_x", cfg.AGV_ROUTE_X)),
        agv_route_y=tuple(float(v) for v in data.get(
            "agv_route_y", cfg.AGV_ROUTE_Y)),
    )


def _unit_a_spec() -> UnitSpec:
    return UnitSpec(
        unit_id=cfg.UNIT_A["unit_id"],
        index=cfg.UNIT_A["index"],
        arm_base=tuple(cfg.UNIT_A["arm_base"]),
        island_pos=tuple(cfg.UNIT_A["island_pos"]),
        station_pos=tuple(cfg.UNIT_A["station_pos"]),
        dock=tuple(cfg.UNIT_A["dock"]),
    )


def _unit_b_spec() -> UnitSpec:
    return UnitSpec(
        unit_id=cfg.UNIT_B["unit_id"],
        index=cfg.UNIT_B["index"],
        arm_base=tuple(cfg.UNIT_B["arm_base"]),
        island_pos=tuple(cfg.UNIT_B["island_pos"]),
        station_pos=tuple(cfg.UNIT_B["station_pos"]),
        dock=tuple(cfg.UNIT_B["dock"]),
    )


L2_MIRROR = Stage3Layout(
    name=cfg.STAGE3_LAYOUT_NAME,
    units=(_unit_a_spec(), _unit_b_spec()),
)

# 候选: 同侧沿 X 错位. B 臂放在传送带 +Y 侧 x=0.40, 只为静态筛选保留;
# 该候选在可达性/AGV dock 压占上即被拒绝, 不进入生产.
L3_SAME_SIDE = Stage3Layout(
    name="L3_SAME_SIDE",
    units=(
        _unit_a_spec(),
        UnitSpec(
            unit_id="B",
            index=2,
            arm_base=(0.40, 0.55, base.ISLAND_TOP_Z),
            island_pos=(0.40, 0.55),
            station_pos=(0.40, 1.25),
            dock=(0.90, 0.55),
        ),
    ),
)

CANDIDATE_LAYOUTS = (L2_MIRROR, L3_SAME_SIDE)


def default_layout() -> Stage3Layout:
    """生产布局 L2_MIRROR."""
    return L2_MIRROR


# ═══════════════════════════════════════════════════════════════
# 静态校验
# ═══════════════════════════════════════════════════════════════

def _rect_clearance(a: Rect, b: Rect) -> float:
    """两个 XY 矩形之间的最小轴向间隙; 重叠返回负值 (负重叠深度)."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx < 0.0 and dy < 0.0:
        return max(dx, dy)
    return max(dx, dy)


def _check(checks: list[dict], name: str, ok: bool,
           detail: str = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})


def validate_layout(layout: Stage3Layout) -> dict:
    """静态几何与粗可达性检查 (不代替 simIK / 碰撞验证).

    检查项:
      - 两个工位的臂基座与岛体一致, 每台臂到 pick/station/dock 可达;
      - Island/Station/Dock/Conveyor/Warehouse 两两不压占;
      - AGV 在 dock 与路线上的占地不压占任何工位/设备;
      - 共享取料区位于传送带上表面有效行程内;
      - AGV 初始朝向与两个 dock 的连线一致.
    """
    checks: list[dict] = []
    _check(checks, "unit_count", len(layout.units) == 2,
           f"units={[u.unit_id for u in layout.units]}")
    ids = {u.unit_id for u in layout.units}
    _check(checks, "unit_ids", ids == {"A", "B"}, f"ids={sorted(ids)}")

    floor = layout.floor_rect
    conveyor = layout.conveyor_rect
    warehouse = layout.warehouse_rect

    for spec in layout.units:
        tag = spec.unit_id
        _check(checks, f"arm_base_on_island:{tag}",
               math.isclose(spec.arm_base[0], spec.island_pos[0])
               and math.isclose(spec.arm_base[1], spec.island_pos[1])
               and math.isclose(spec.arm_base[2], spec.island_top_z),
               f"arm={spec.arm_base} island={spec.island_pos} "
               f"top_z={spec.island_top_z}")
        for name, rect in (("island", spec.island_rect),
                           ("station", spec.station_rect),
                           ("dock", spec.dock_rect)):
            _check(checks, f"floor_bounds:{tag}:{name}",
                   rect_inside(rect, floor),
                   f"{tuple(round(v, 4) for v in rect)}")
        for name, target in spec.targets().items():
            distance = math.dist(spec.arm_base, target[:3])
            _check(checks, f"arm_reach:{tag}:{name}",
                   distance <= base.REACH_LIMIT,
                   f"{distance:.3f} m <= {base.REACH_LIMIT:.3f} m")

        for name, other in (("conveyor", conveyor),
                            ("warehouse", warehouse)):
            gap = _rect_clearance(spec.island_rect, other)
            _check(checks, f"island_vs_{name}:{tag}",
                   gap >= cfg.LAYOUT_MIN_CLEARANCE,
                   f"gap={gap:.4f} m")
            gap = _rect_clearance(spec.station_rect, other)
            _check(checks, f"station_vs_{name}:{tag}",
                   gap >= cfg.LAYOUT_MIN_CLEARANCE,
                   f"gap={gap:.4f} m")
            gap = _rect_clearance(spec.dock_rect, other)
            _check(checks, f"dock_vs_{name}:{tag}",
                   gap >= cfg.LAYOUT_MIN_CLEARANCE,
                   f"gap={gap:.4f} m")

        gap = _rect_clearance(spec.station_rect, spec.island_rect)
        _check(checks, f"station_vs_island:{tag}",
               gap >= cfg.LAYOUT_MIN_CLEARANCE, f"gap={gap:.4f} m")

    specs = list(layout.units)
    for i, a in enumerate(specs):
        for b in specs[i + 1:]:
            for name_a, rect_a in (("island", a.island_rect),
                                   ("station", a.station_rect),
                                   ("dock", a.dock_rect)):
                for name_b, rect_b in (("island", b.island_rect),
                                       ("station", b.station_rect),
                                       ("dock", b.dock_rect)):
                    gap = _rect_clearance(rect_a, rect_b)
                    _check(checks,
                           f"{name_a}_{a.unit_id}_vs_{name_b}_{b.unit_id}",
                           gap >= cfg.LAYOUT_MIN_CLEARANCE,
                           f"gap={gap:.4f} m")

    # 共享取料区在传送带上表面有效行程内
    stop_local = layout.pick_zone_center[0] - layout.conveyor_center[0]
    _check(checks, "pick_zone_on_conveyor",
           base.CONVEYOR_TOP_RUN_LOCAL[0] <= stop_local
           <= base.CONVEYOR_TOP_RUN_LOCAL[1],
           f"local_x={stop_local:.4f}")
    _check(checks, "pick_zone_shared",
           all(math.dist(spec.arm_base, layout.pick_zone_center)
               <= base.REACH_LIMIT for spec in specs),
           f"pick={layout.pick_zone_center}")

    # AGV 路线扫掠矩形
    route = layout.agv_route_rect
    _check(checks, "agv_route_floor", rect_inside(route, floor),
           f"{tuple(round(v, 4) for v in route)}")
    for spec in specs:
        for name, rect in (("island", spec.island_rect),
                           ("station", spec.station_rect)):
            gap = _rect_clearance(route, rect)
            _check(checks, f"agv_route_vs_{name}:{spec.unit_id}",
                   gap >= cfg.LAYOUT_MIN_CLEARANCE, f"gap={gap:.4f} m")
    for name, rect in (("conveyor", conveyor), ("warehouse", warehouse)):
        gap = _rect_clearance(route, rect)
        _check(checks, f"agv_route_vs_{name}",
               gap >= cfg.LAYOUT_MIN_CLEARANCE, f"gap={gap:.4f} m")

    # AGV 朝向: 两个 dock 的连线应沿 -Y; 车体长轴沿 Y.
    route_dx = max(abs(layout.agv_route_x - spec.dock[0]) for spec in specs)
    route_dy = abs(specs[1].dock[1] - specs[0].dock[1])
    _check(checks, "agv_route_axis",
           route_dx <= 1e-6 and route_dy > 0.5,
           f"dx={route_dx:.6f} dy={route_dy:.3f}")
    _check(checks, "agv_initial_heading",
           abs(abs(layout.agv_initial_yaw) - math.pi / 2.0) <= 1e-6,
           f"yaw={layout.agv_initial_yaw:.6f}")

    return {"ok": all(c["ok"] for c in checks),
            "checks": checks, "layout": layout.name}


def screen_candidates(candidates=None) -> dict:
    """对候选布局做静态筛选; 返回每个候选的完整检查与选中结果."""
    candidates = list(candidates or CANDIDATE_LAYOUTS)
    rows = []
    selected = None
    for layout in candidates:
        report = validate_layout(layout)
        row = {
            "name": layout.name,
            "ok": bool(report["ok"]),
            "failed": [c for c in report["checks"] if not c["ok"]],
            "checks": len(report["checks"]),
            "selected": False,
        }
        if report["ok"] and selected is None:
            selected = layout.name
            row["selected"] = True
        rows.append(row)
    return {
        "selected": selected,
        "candidates": rows,
        "note": "静态筛选不代替 simIK/碰撞查询; 生产布局以 v1 启动点校验为准",
    }
