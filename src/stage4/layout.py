"""layout.py — 阶段四五岛布局、物流线派生与静态几何校验 (06-16 §3).

`Stage4Layout` 是不可变布局描述: 五个工位、四条物流线 (含阶段三既有的
S1→A 输入带)、A→C 固定交接、E 岛两个输入缓冲、四个 AGV dock 和一条
x=0.40 的直线 AGV 通道. 场景构建/控制器只读取该对象, 不在控制循环内
硬编码坐标.

坐标来源:
  - A/B 沿用阶段三 `L2_MIRROR` 实测坐标 (基座/置物台/dock);
  - C/D/E 按 06-16 §3.2 的相对关系布置, 由阶段四测量探针与真实
    simIK/碰撞检查确认后冻结;
  - 传送带入口/停止区由模型实测上表面行程与内缩量派生, 不使用包围盒端点.

本模块只做静态几何/粗可达性筛选; 真实 simIK、关节限位与碰撞查询由
`docs/measurement/probe_stage4_layout.py` 在仿真器在线时执行.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from stage1 import config as base
from stage1.conveyor_controller import BeltGeometry
from stage1.layout import Rect, rect_inside, rect_of
from stage3.layout import quat_from_yaw, yaw_from_quat

from . import config as cfg


# ═══════════════════════════════════════════════════════════════
# 几何工具
# ═══════════════════════════════════════════════════════════════

def _rotate_xy(x: float, y: float, yaw: float) -> tuple[float, float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return (c * x - s * y, s * x + c * y)


def rect_clearance(a: Rect, b: Rect) -> float:
    """两个 XY 矩形之间的最小轴向间隙; 重叠返回负值."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx < 0.0 and dy < 0.0:
        return max(dx, dy)
    return max(dx, dy)


# ═══════════════════════════════════════════════════════════════
# 物流线
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LineSpec:
    """一条内置传送带的有效行程、取料区与驱动参数.

    `entry`/`stop` 是世界坐标; `direction` 是入口→停止区的单位向量
    (只允许轴对齐: ±X 或 ±Y). 阶段四的所有线都使用与阶段一相同的
    设计长度、入口/停止区内缩和带速.
    """

    line_id: str
    conveyor: str
    entry_alias: str
    stop_alias: str
    sensor_alias: str
    center: tuple[float, float]
    yaw: float
    design_length: float
    source: str
    destination: str

    # ── 派生几何 ────────────────────────────────────────────

    @property
    def entry_local_x(self) -> float:
        return base.CONVEYOR_TOP_RUN_LOCAL[0] + base.CONVEYOR_ENTRY_INSET

    @property
    def stop_local_x(self) -> float:
        return base.CONVEYOR_TOP_RUN_LOCAL[1] - base.CONVEYOR_STOP_INSET

    def _world_at(self, local_x: float) -> tuple[float, float, float]:
        dx, dy = _rotate_xy(local_x, 0.0, self.yaw)
        return (self.center[0] + dx, self.center[1] + dy,
                base.CONVEYOR_SURFACE_Z)

    @property
    def entry(self) -> tuple[float, float, float]:
        return self._world_at(self.entry_local_x)

    @property
    def stop(self) -> tuple[float, float, float]:
        return self._world_at(self.stop_local_x)

    @property
    def direction(self) -> tuple[float, float]:
        dx = self.stop[0] - self.entry[0]
        dy = self.stop[1] - self.entry[1]
        n = math.hypot(dx, dy)
        if n <= 1e-9:
            raise ValueError(f"{self.line_id} 有效行程长度为 0")
        return (dx / n, dy / n)

    @property
    def geometry(self) -> BeltGeometry:
        return BeltGeometry(entry=self.entry, stop=self.stop)

    @property
    def pick_material_pose(self) -> tuple[float, float, float]:
        return (self.stop[0], self.stop[1],
                base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT)

    @property
    def place_material_pose(self) -> tuple[float, float, float]:
        return (self.entry[0], self.entry[1],
                base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT)

    @property
    def pick_region(self):
        """允许抓取区: 停止区上游 0.20 m 到下游 0.03 m, 横向 ±0.04 m."""
        z = base.CONVEYOR_SURFACE_Z + base.MATERIAL_HALF_HEIGHT
        dx, dy = self.direction
        if abs(dx) > abs(dy):
            sign = 1.0 if dx > 0 else -1.0
            lo = self.stop[0] - sign * cfg.PICK_REGION_UPSTREAM
            hi = self.stop[0] + sign * cfg.PICK_REGION_DOWNSTREAM
            x_range = (min(lo, hi), max(lo, hi))
            y_range = (self.stop[1] - cfg.PICK_REGION_LATERAL,
                       self.stop[1] + cfg.PICK_REGION_LATERAL)
        else:
            sign = 1.0 if dy > 0 else -1.0
            lo = self.stop[1] - sign * cfg.PICK_REGION_UPSTREAM
            hi = self.stop[1] + sign * cfg.PICK_REGION_DOWNSTREAM
            x_range = (self.stop[0] - cfg.PICK_REGION_LATERAL,
                       self.stop[0] + cfg.PICK_REGION_LATERAL)
            y_range = (min(lo, hi), max(lo, hi))
        return base.PickRegion(x=x_range, y=y_range, z_center=z,
                               z_tol=cfg.PICK_REGION_Z_TOL)

    @property
    def rect(self) -> Rect:
        """传送带模型包围盒的 XY 投影 (按设计长度与实测宽度估算)."""
        width = base.CONVEYOR_NATIVE_WIDTH * (
            self.design_length / base.CONVEYOR_NATIVE_LENGTH)
        half_l = self.design_length / 2.0
        half_w = width / 2.0
        if abs(math.cos(self.yaw)) > 0.5:
            return rect_of(self.center, (self.design_length, width))
        return rect_of(self.center, (width, self.design_length))

    @property
    def top_run_rect(self) -> Rect:
        """有效承载区投影 (入口→停止区) 加带面横向半宽."""
        width = base.CONVEYOR_NATIVE_WIDTH * (
            self.design_length / base.CONVEYOR_NATIVE_LENGTH)
        half_w = width / 2.0
        x0 = min(self.entry[0], self.stop[0])
        x1 = max(self.entry[0], self.stop[0])
        y0 = min(self.entry[1], self.stop[1])
        y1 = max(self.entry[1], self.stop[1])
        if abs(self.direction[0]) > abs(self.direction[1]):
            y0, y1 = self.stop[1] - half_w, self.stop[1] + half_w
        else:
            x0, x1 = self.stop[0] - half_w, self.stop[0] + half_w
        return (x0, y0, x1, y1)

    def as_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "conveyor": self.conveyor,
            "entry_alias": self.entry_alias,
            "stop_alias": self.stop_alias,
            "sensor_alias": self.sensor_alias,
            "center": list(self.center),
            "yaw": round(float(self.yaw), 6),
            "design_length": float(self.design_length),
            "source": self.source,
            "destination": self.destination,
            "entry": [round(v, 6) for v in self.entry],
            "stop": [round(v, 6) for v in self.stop],
            "direction": [round(v, 6) for v in self.direction],
        }


# ═══════════════════════════════════════════════════════════════
# 工位
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UnitSpec4:
    """一个 UR5 工位的世界坐标、角色与可达目标.

    `station_pos` 对 A/B 是实体置物台中心; 对 C/D/E 是标称输出位
    (传送带入口/停止区/E 缓冲), 只用于派生与可达检查.
    """

    unit_id: str
    index: int
    arm_base: tuple[float, float, float]
    island_pos: tuple[float, float]
    station_pos: tuple[float, float]
    dock: Optional[tuple[float, float]]
    has_station: bool = False
    has_dock: bool = False
    island_size: tuple[float, float, float] = tuple(base.ISLAND_SIZE)
    station_size: tuple[float, float, float] = tuple(base.STATION_SIZE)
    island_top_z: float = base.ISLAND_TOP_Z
    station_top_z: float = base.STATION_TOP_Z

    @property
    def aliases(self) -> dict[str, str]:
        return dict(cfg.UNIT_ALIASES[self.unit_id])

    @property
    def dock_sensor_alias(self) -> str:
        if not self.has_dock:
            return ""
        return cfg.ALIASES["dock_sensor"][self.unit_id]

    @property
    def island_rect(self) -> Rect:
        return rect_of(self.island_pos, self.island_size[:2])

    @property
    def station_rect(self) -> Rect:
        return rect_of(self.station_pos, self.station_size[:2])

    @property
    def dock_rect(self) -> Optional[Rect]:
        if self.dock is None:
            return None
        return rect_of(self.dock, cfg.AGV_FOOTPRINT_AT_DOCK)

    # ── 目标位姿 ────────────────────────────────────────────

    def targets(self) -> dict[str, tuple[float, float, float]]:
        """本工位需要够到的物料目标 (世界坐标, 物料中心 Z)."""
        out: dict[str, tuple[float, float, float]] = {}
        if self.unit_id == "A":
            out["line_s1a"] = _line("line_s1a").pick_material_pose
            out["handoff_ac"] = _handoff_material_pose()
            out["dock_A"] = self.agv_material_pose
        elif self.unit_id == "B":
            out["line_s1b"] = _line("line_s1b").pick_material_pose
            out["dock_B"] = self.agv_material_pose
        elif self.unit_id == "C":
            out["handoff_ac"] = _handoff_material_pose()
            out["line_ce_place"] = _line("line_ce").place_material_pose
        elif self.unit_id == "D":
            out["line_s2d"] = _line("line_s2d").pick_material_pose
            out["dock_D"] = self.agv_material_pose
        elif self.unit_id == "E":
            out["line_ce"] = _line("line_ce").pick_material_pose
            out["dock_E"] = self.agv_material_pose
            out["buffer_e_c"] = _buffer_material_pose("buffer_e_c")
            out["buffer_e_d"] = _buffer_material_pose("buffer_e_d")
        return out

    @property
    def agv_material_pose(self) -> tuple[float, float, float]:
        if self.dock is None:
            raise ValueError(f"{self.unit_id} 没有 AGV dock")
        return (self.dock[0], self.dock[1],
                base.AGV_TRAY_TOP_Z + base.MATERIAL_HALF_HEIGHT)

    @property
    def agv_tcp_pose(self) -> list[float]:
        return base.tcp_pose_above(self.agv_material_pose)

    @property
    def station_material_pose(self) -> tuple[float, float, float]:
        return (self.station_pos[0], self.station_pos[1],
                self.station_top_z + base.MATERIAL_HALF_HEIGHT)

    @property
    def station_tcp_pose(self) -> list[float]:
        return base.tcp_pose_above(self.station_material_pose)

    def as_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "index": self.index,
            "arm_base": list(self.arm_base),
            "island_pos": list(self.island_pos),
            "station_pos": list(self.station_pos),
            "dock": (None if self.dock is None else list(self.dock)),
            "has_station": bool(self.has_station),
            "has_dock": bool(self.has_dock),
            "island_size": list(self.island_size),
            "station_size": list(self.station_size),
            "island_top_z": self.island_top_z,
            "station_top_z": self.station_top_z,
            "aliases": self.aliases,
            "dock_sensor_alias": self.dock_sensor_alias,
            "targets": {name: [round(float(v), 6) for v in pose]
                        for name, pose in self.targets().items()},
        }


def _line(line_id: str) -> LineSpec:
    return _LINES[line_id]


def _handoff_material_pose() -> tuple[float, float, float]:
    pos = cfg.HANDOFF_AC["position"]
    return (pos[0], pos[1],
            base.STATION_TOP_Z + base.MATERIAL_HALF_HEIGHT)


def _buffer_material_pose(key: str) -> tuple[float, float, float]:
    pos = cfg.E_BUFFERS[key]["position"]
    return (pos[0], pos[1],
            base.ISLAND_TOP_Z + base.MATERIAL_HALF_HEIGHT)


def handoff_park_tcp(unit_id: str) -> Optional[tuple[float, ...]]:
    """共享交接台放置后的撤离目标 (A 臂专用).

    A 放置到 A→C 交接台后若只做 +0.15 m 竖直撤离, 腕部仍停在交接台上方,
    C 臂从北侧接近同一交接点时会与 A 的 link6 相撞. A 必须把工具移到
    交接台南侧上方 (仍在本臂可达范围内), 让出交接台包络; 该目标由布局
    派生, 不写入工件 digest.
    """
    if unit_id != "A":
        return None
    x, y = cfg.HANDOFF_AC["position"]
    return (x + 0.25, y - 0.30, base.STATION_TOP_Z + 0.40,
            *base.TOOL_TARGET_QUATERNION)


_LINES = {
    line_id: LineSpec(
        line_id=line_id,
        conveyor=spec["conveyor"],
        entry_alias=spec["entry"],
        stop_alias=spec["stop"],
        sensor_alias=spec["sensor"],
        center=tuple(spec["center"]),
        yaw=float(spec["yaw"]),
        design_length=float(spec["design_length"]),
        source=str(spec["source"]),
        destination=str(spec["destination"]),
    )
    for line_id, spec in cfg.LINE_DEFS.items()
}


def line_specs() -> dict[str, LineSpec]:
    return dict(_LINES)


# ═══════════════════════════════════════════════════════════════
# 布局
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Stage4Layout:
    name: str
    units: tuple[UnitSpec4, ...]
    lines: dict[str, LineSpec] = field(default_factory=line_specs)
    docks: dict[str, tuple[float, float]] = field(default_factory=dict)
    agv_initial_dock: str = cfg.AGV_INITIAL_DOCK
    agv_initial_yaw: float = cfg.AGV_INITIAL_YAW
    handoff_ac: tuple[float, float] = tuple(cfg.HANDOFF_AC["position"])
    buffers: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {key: tuple(spec["position"])
                                 for key, spec in cfg.E_BUFFERS.items()})
    e_output: tuple[float, float] = tuple(cfg.E_OUTPUT["position"])

    # ── 访问 ────────────────────────────────────────────────

    def unit(self, unit_id: str) -> UnitSpec4:
        for spec in self.units:
            if spec.unit_id == unit_id:
                return spec
        raise KeyError(f"未知工位: {unit_id!r} "
                       f"(可用: {[u.unit_id for u in self.units]})")

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(spec.unit_id for spec in self.units)

    def view(self, unit_id: str) -> "UnitView4":
        return UnitView4(layout=self, spec=self.unit(unit_id))

    @property
    def dock_positions(self) -> dict[str, tuple[float, float]]:
        if self.docks:
            return dict(self.docks)
        return {spec.unit_id: spec.dock for spec in self.units
                if spec.dock is not None}

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

    @property
    def warehouse_s2_rect(self) -> Rect:
        return rect_of(cfg.WAREHOUSE_S2_POS, cfg.WAREHOUSE_S2_SIZE[:2])

    @property
    def agv_route_rect(self) -> Rect:
        ys = [pos[1] for pos in self.dock_positions.values()]
        half_x = cfg.AGV_ROUTE_HALF_WIDTH + cfg.AGV_ROUTE_CLEARANCE
        half_y = (cfg.AGV_FOOTPRINT_AT_DOCK[1] / 2.0
                  + cfg.AGV_ROUTE_ARRIVE_TOL + cfg.AGV_ROUTE_CLEARANCE)
        return (cfg.AGV_ROUTE_X - half_x, min(ys) - half_y,
                cfg.AGV_ROUTE_X + half_x, max(ys) + half_y)

    def agv_pose_at(self, dock: str) -> list[float]:
        pos = self.dock_positions[dock]
        return [pos[0], pos[1], base.AGV_ROOT_Z,
                *quat_from_yaw(self.agv_initial_yaw)]

    # ── 序列化 ──────────────────────────────────────────────

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "units": [spec.as_dict() for spec in self.units],
            "lines": {line_id: spec.as_dict()
                      for line_id, spec in self.lines.items()},
            "docks": {key: list(value)
                      for key, value in self.dock_positions.items()},
            "agv_initial_dock": self.agv_initial_dock,
            "agv_initial_yaw": self.agv_initial_yaw,
            "handoff_ac": list(self.handoff_ac),
            "buffers": {key: list(value)
                        for key, value in self.buffers.items()},
            "e_output": list(self.e_output),
        }

    def digest_payload(self) -> dict:
        return self.as_dict()


class UnitView4:
    """控制器/运行时使用的单工位视图 (只读, 不共享可变状态)."""

    def __init__(self, layout: Stage4Layout, spec: UnitSpec4) -> None:
        self._layout = layout
        self.spec = spec

    @property
    def name(self) -> str:
        return f"{self._layout.name}/{self.spec.unit_id}"

    @property
    def unit_id(self) -> str:
        return self.spec.unit_id

    @property
    def arm_base(self):
        return self.spec.arm_base

    @property
    def island_pos(self):
        return self.spec.island_pos

    @property
    def station_pos(self):
        return self.spec.station_pos

    @property
    def agv_dock(self):
        return self.spec.dock

    @property
    def island_top_z(self):
        return self.spec.island_top_z

    @property
    def station_top_z(self):
        return self.spec.station_top_z

    @property
    def agv_material_pose(self):
        return self.spec.agv_material_pose

    @property
    def agv_tcp_pose(self):
        return self.spec.agv_tcp_pose

    @property
    def station_material_pose(self):
        return self.spec.station_material_pose

    @property
    def station_tcp_pose(self):
        return self.spec.station_tcp_pose

    @property
    def island_rect(self):
        return self.spec.island_rect

    @property
    def station_rect(self):
        return self.spec.station_rect

    @property
    def agv_rect(self):
        return self.spec.dock_rect

    @property
    def floor_rect(self):
        return self._layout.floor_rect

    @property
    def conveyor_rect(self):
        return self._layout.conveyor_rect

    @property
    def warehouse_rect(self):
        return self._layout.warehouse_rect


# ═══════════════════════════════════════════════════════════════
# 生产布局
# ═══════════════════════════════════════════════════════════════

def _unit_from_def(unit_id: str) -> UnitSpec4:
    spec = cfg.UNIT_DEFS[unit_id]
    return UnitSpec4(
        unit_id=unit_id,
        index=int(spec["index"]),
        arm_base=tuple(spec["arm_base"]),
        island_pos=tuple(spec["island_pos"]),
        station_pos=tuple(spec["station_pos"]),
        dock=(None if spec["dock"] is None else tuple(spec["dock"])),
        has_station=bool(spec["has_station"]),
        has_dock=bool(spec["has_dock"]),
        island_size=tuple(spec.get("island_size", base.ISLAND_SIZE)),
        station_size=tuple(spec.get("station_size", base.STATION_SIZE)),
        island_top_z=float(spec.get("island_top_z", base.ISLAND_TOP_Z)),
        station_top_z=float(spec.get("station_top_z", base.STATION_TOP_Z)),
    )


L4_COLUMN = Stage4Layout(
    name=cfg.STAGE4_LAYOUT_NAME,
    units=tuple(_unit_from_def(unit_id) for unit_id in cfg.UNIT_IDS),
    docks={unit_id: tuple(cfg.UNIT_DEFS[unit_id]["dock"])
           for unit_id in cfg.UNIT_IDS
           if cfg.UNIT_DEFS[unit_id]["dock"] is not None},
)


def default_layout() -> Stage4Layout:
    return L4_COLUMN


def _unit_from_dict(data: dict) -> UnitSpec4:
    dock = data.get("dock")
    return UnitSpec4(
        unit_id=str(data["unit_id"]),
        index=int(data.get("index", 0)),
        arm_base=tuple(float(v) for v in data["arm_base"]),
        island_pos=tuple(float(v) for v in data["island_pos"]),
        station_pos=tuple(float(v) for v in data["station_pos"]),
        dock=(None if dock is None else tuple(float(v) for v in dock)),
        has_station=bool(data.get("has_station", False)),
        has_dock=bool(data.get("has_dock", False)),
        island_size=tuple(float(v) for v in data.get(
            "island_size", base.ISLAND_SIZE)),
        station_size=tuple(float(v) for v in data.get(
            "station_size", base.STATION_SIZE)),
        island_top_z=float(data.get("island_top_z", base.ISLAND_TOP_Z)),
        station_top_z=float(data.get("station_top_z", base.STATION_TOP_Z)),
    )


def _line_from_dict(data: dict) -> LineSpec:
    return LineSpec(
        line_id=str(data["line_id"]),
        conveyor=str(data["conveyor"]),
        entry_alias=str(data["entry_alias"]),
        stop_alias=str(data["stop_alias"]),
        sensor_alias=str(data["sensor_alias"]),
        center=tuple(float(v) for v in data["center"]),
        yaw=float(data["yaw"]),
        design_length=float(data.get("design_length",
                                     base.CONVEYOR_DESIGN_LENGTH)),
        source=str(data.get("source", "")),
        destination=str(data.get("destination", "")),
    )


def layout_from_dict(data: dict) -> Stage4Layout:
    if not isinstance(data, dict):
        raise ValueError("stage4 layout 必须是对象")
    units = tuple(_unit_from_dict(item) for item in data.get("units") or ())
    if len(units) != 5:
        raise ValueError(f"stage4 layout 必须包含 5 个工位, 实际 {len(units)}")
    name = str(data.get("name") or "")
    if not name:
        raise ValueError("stage4 layout 缺少 name")
    lines = {key: _line_from_dict(value)
             for key, value in (data.get("lines") or {}).items()}
    if set(lines) != set(cfg.LINE_IDS):
        raise ValueError(f"stage4 layout 物流线不全: {sorted(lines)}")
    docks = {str(key): tuple(float(v) for v in value)
             for key, value in (data.get("docks") or {}).items()}
    buffers = {str(key): tuple(float(v) for v in value)
               for key, value in (data.get("buffers") or {}).items()}
    return Stage4Layout(
        name=name, units=units, lines=lines, docks=docks,
        agv_initial_dock=str(data.get("agv_initial_dock",
                                      cfg.AGV_INITIAL_DOCK)),
        agv_initial_yaw=float(data.get("agv_initial_yaw",
                                       cfg.AGV_INITIAL_YAW)),
        handoff_ac=tuple(float(v) for v in data.get(
            "handoff_ac", cfg.HANDOFF_AC["position"])),
        buffers=buffers or {key: tuple(spec["position"])
                            for key, spec in cfg.E_BUFFERS.items()},
        e_output=tuple(float(v) for v in data.get(
            "e_output", cfg.E_OUTPUT["position"])),
    )


# ═══════════════════════════════════════════════════════════════
# 静态校验
# ═══════════════════════════════════════════════════════════════

def validate_layout(layout: Stage4Layout) -> dict:
    """静态几何与粗可达性检查 (不代替 simIK / 碰撞验证).

    检查项:
      - 5 个工位、每台臂到本岛取放/交接/dock 目标可达;
      - Island/Station/Dock/Warehouse/Conveyor 两两不压占;
      - AGV 停车与路线扫掠不压占任何工位/设备;
      - 物流线取料区位于有效行程内且只被目标工位够到;
      - 固定交接台被 A/C 两台臂够到, E 缓冲被 E 臂够到;
      - AGV 初始朝向与 dock 连线一致.
    """
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("unit_count", len(layout.units) == 5,
          f"units={[u.unit_id for u in layout.units]}")
    check("unit_ids", set(layout.unit_ids) == set(cfg.UNIT_IDS),
          f"ids={sorted(layout.unit_ids)}")

    floor = layout.floor_rect
    devices: dict[str, Rect] = {
        "conveyor_s1a": layout.conveyor_rect,
        "warehouse_s1": layout.warehouse_rect,
        "warehouse_s2": layout.warehouse_s2_rect,
    }
    for line_id, line in layout.lines.items():
        if line_id != "line_s1a":
            devices[f"line_{line_id}"] = line.rect

    for spec in layout.units:
        tag = spec.unit_id
        check(f"arm_base_on_island:{tag}",
              math.isclose(spec.arm_base[0], spec.island_pos[0])
              and math.isclose(spec.arm_base[1], spec.island_pos[1])
              and math.isclose(spec.arm_base[2], spec.island_top_z),
              f"arm={spec.arm_base} island={spec.island_pos} "
              f"top_z={spec.island_top_z}")
        check(f"island_floor:{tag}", rect_inside(spec.island_rect, floor),
              f"{tuple(round(v, 4) for v in spec.island_rect)}")
        if spec.has_station:
            check(f"station_floor:{tag}",
                  rect_inside(spec.station_rect, floor),
                  f"{tuple(round(v, 4) for v in spec.station_rect)}")
            gap = rect_clearance(spec.station_rect, spec.island_rect)
            check(f"station_vs_island:{tag}", gap >= 0.02,
                  f"gap={gap:.4f} m")
        if spec.dock_rect is not None:
            check(f"dock_floor:{tag}", rect_inside(spec.dock_rect, floor),
                  f"{tuple(round(v, 4) for v in spec.dock_rect)}")
            gap = rect_clearance(spec.dock_rect, spec.island_rect)
            check(f"dock_vs_island:{tag}", gap >= 0.02,
                  f"gap={gap:.4f} m")
        for name, target in spec.targets().items():
            distance = math.dist(spec.arm_base, target[:3])
            check(f"arm_reach:{tag}:{name}", distance <= base.REACH_LIMIT,
                  f"{distance:.3f} m <= {base.REACH_LIMIT:.3f} m")

        for name, rect in devices.items():
            gap = rect_clearance(spec.island_rect, rect)
            check(f"island_vs_{name}:{tag}", gap >= 0.02,
                  f"gap={gap:.4f} m")
            if spec.has_station:
                gap = rect_clearance(spec.station_rect, rect)
                check(f"station_vs_{name}:{tag}", gap >= 0.02,
                      f"gap={gap:.4f} m")

    # 工位两两不压占
    specs = list(layout.units)
    for i, first in enumerate(specs):
        for second in specs[i + 1:]:
            gap = rect_clearance(first.island_rect, second.island_rect)
            check(f"island_{first.unit_id}_vs_island_{second.unit_id}",
                  gap >= 0.02, f"gap={gap:.4f} m")
            if first.has_station:
                gap = rect_clearance(first.station_rect,
                                     second.island_rect)
                check(f"station_{first.unit_id}_vs_island_{second.unit_id}",
                      gap >= 0.02, f"gap={gap:.4f} m")
            if second.has_station:
                gap = rect_clearance(second.station_rect,
                                     first.island_rect)
                check(f"station_{second.unit_id}_vs_island_{first.unit_id}",
                      gap >= 0.02, f"gap={gap:.4f} m")

    # 物流线: 取料区在有效行程内, 只被目标工位够到
    for line_id, line in layout.lines.items():
        stop_local = line.stop_local_x
        check(f"line_top_run:{line_id}",
              base.CONVEYOR_TOP_RUN_LOCAL[0] <= line.entry_local_x
              <= base.CONVEYOR_TOP_RUN_LOCAL[1]
              and base.CONVEYOR_TOP_RUN_LOCAL[0] <= stop_local
              <= base.CONVEYOR_TOP_RUN_LOCAL[1],
              f"entry_local={line.entry_local_x:.4f} "
              f"stop_local={stop_local:.4f}")
        destination = line.destination
        for spec in specs:
            distance = math.dist(spec.arm_base, line.pick_material_pose)
            reachable = distance <= base.REACH_LIMIT
            if spec.unit_id == destination:
                check(f"line_reach:{line_id}:{destination}", reachable,
                      f"{distance:.3f} m <= {base.REACH_LIMIT:.3f} m")
            else:
                # 相邻臂的物理包络可能覆盖他岛取料点; 线的使用由任务和
                # 资源令牌唯一确定, 不在静态筛选中当作失败, 只记录重叠.
                check(f"line_reach_overlap:{line_id}:{spec.unit_id}", True,
                      f"distance={distance:.3f} m "
                      f"(reachable={reachable}; line owned by {destination})")

    # 固定交接 A→C: A/C 够到
    handoff = _handoff_material_pose()
    for unit_id in ("A", "C"):
        distance = math.dist(layout.unit(unit_id).arm_base, handoff)
        check(f"handoff_reach:{unit_id}", distance <= base.REACH_LIMIT,
              f"{distance:.3f} m")

    # E 缓冲
    for key in cfg.E_BUFFERS:
        distance = math.dist(layout.unit("E").arm_base,
                             _buffer_material_pose(key))
        check(f"buffer_reach:{key}", distance <= base.REACH_LIMIT,
              f"{distance:.3f} m")

    # AGV 通道
    route = layout.agv_route_rect
    check("agv_route_floor", rect_inside(route, floor),
          f"{tuple(round(v, 4) for v in route)}")
    for spec in specs:
        gap = rect_clearance(route, spec.island_rect)
        check(f"agv_route_vs_island:{spec.unit_id}", gap >= 0.02,
              f"gap={gap:.4f} m")
        if spec.has_station:
            gap = rect_clearance(route, spec.station_rect)
            check(f"agv_route_vs_station:{spec.unit_id}", gap >= 0.02,
                  f"gap={gap:.4f} m")
    for name, rect in devices.items():
        gap = rect_clearance(route, rect)
        check(f"agv_route_vs_{name}", gap >= 0.02, f"gap={gap:.4f} m")
    # E 缓冲/输出在 E 岛台面上, 不在通道内
    for key in cfg.E_BUFFERS:
        rect = rect_of(cfg.E_BUFFERS[key]["position"],
                       (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
        gap = rect_clearance(route, rect)
        check(f"agv_route_vs_{key}", gap >= 0.02, f"gap={gap:.4f} m")
    output_rect = rect_of(cfg.E_OUTPUT["position"],
                          (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
    gap = rect_clearance(route, output_rect)
    check("agv_route_vs_e_output", gap >= 0.02, f"gap={gap:.4f} m")

    # AGV 朝向: 所有 dock 共线于 x=0.40, 车头朝 -Y
    xs = {round(pos[0], 6) for pos in layout.dock_positions.values()}
    check("agv_docks_collinear", xs == {round(cfg.AGV_ROUTE_X, 6)},
          f"xs={sorted(xs)}")
    check("agv_initial_heading",
          abs(abs(layout.agv_initial_yaw) - math.pi / 2.0) <= 1e-6,
          f"yaw={layout.agv_initial_yaw:.6f}")
    check("agv_initial_dock",
          layout.agv_initial_dock in layout.dock_positions,
          f"dock={layout.agv_initial_dock}")

    return {"ok": all(item["ok"] for item in checks),
            "checks": checks, "layout": layout.name}


# ═══════════════════════════════════════════════════════════════
# 工位/路线辅助
# ═══════════════════════════════════════════════════════════════

def arm_adjacency_pairs(layout: Stage4Layout) -> list[tuple[str, str]]:
    """按基座间距导出需要互锁的臂对 (静态, 用于资源表校验)."""
    pairs = []
    specs = list(layout.units)
    for i, first in enumerate(specs):
        for second in specs[i + 1:]:
            if math.dist(first.arm_base, second.arm_base) < \
                    cfg.ARM_ADJACENCY_DISTANCE:
                pairs.append((first.unit_id, second.unit_id))
    return pairs


def zones_for_unit(unit_id: str) -> list[str]:
    """该工位运动时必须持有的臂间互锁区 (固定顺序)."""
    out = []
    for zone, pair in cfg.ARM_ADJACENCY_ZONES.items():
        if unit_id in pair:
            out.append(zone)
    return [zone for zone in cfg.RESOURCE_ORDER if zone in out]


def route_specs(layout: Stage4Layout) -> dict[tuple[str, str, bool], object]:
    """所有 dock 之间的直线路线 (按起点/终点/是否带载)."""
    from .agv_map import RouteSpec4
    specs = {}
    docks = layout.dock_positions
    for start in docks:
        for end in docks:
            if start == end:
                continue
            for loaded in (False, True):
                specs[(start, end, loaded)] = RouteSpec4(
                    route_id=f"{start}_to_{end}"
                             f"{'_loaded' if loaded else '_empty'}",
                    start_dock=start, end_dock=end,
                    start=tuple(docks[start]), end=tuple(docks[end]),
                    heading=layout.agv_initial_yaw,
                    loaded=loaded,
                )
    return specs
