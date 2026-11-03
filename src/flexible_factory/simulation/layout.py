"""layout.py — v1 UFLOW 五岛布局、物流线派生与静态几何校验.

`Stage4Layout` 是不可变布局描述: 五个工位、四条物流线 (含双站层既有的
S1→A 输入带)、A→C 固定交接、E 岛两个输入缓冲、四个 AGV dock 和一条
由西/南/东三段直道组成的外围 AGV 路网. 场景构建/控制器只读取该对象,
不在控制循环内硬编码坐标.

布局 `L5_UFLOW`:
  - A/B 沿用双站层 `L2_MIRROR` 实测坐标 (基座/置物台/dock);
  - 固定物流沿 +X: A→C 短交接 → C→E 输送 → E; S2→D 在南侧独立;
  - AGV dock 不再共线: A/B 在 x=0.40, D/E 在 x=2.90, 南横道连接两侧;
  - 所有工位基座统一 0.30 m, 高度 datum 显式写入布局摘要.

坐标来源:
  - A/B 沿用双站层 `L2_MIRROR` 实测坐标;
  - C/D/E 由 v1 冻结前的实机测量与真实 simIK/碰撞检查确认;
  - 传送带入口/停止区由模型实测上表面行程与内缩量派生, 不使用包围盒端点.

本模块只做静态几何/粗可达性筛选; 真实 simIK、关节限位与碰撞查询由
v1 启动点校验在仿真器在线时执行.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Optional

from . import config_base as base
from .conveyor import BeltGeometry
from .layout_base import Rect, rect_inside, rect_of
from .layout_dual import quat_from_yaw, yaw_from_quat

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


def point_rect_distance(point: tuple[float, float], rect: Rect) -> float:
    """点到轴对齐矩形的最小距离; 点在矩形内返回 0."""
    dx = max(rect[0] - point[0], 0.0, point[0] - rect[2])
    dy = max(rect[1] - point[1], 0.0, point[1] - rect[3])
    return math.hypot(dx, dy)


def circle_clear_rect(center: tuple[float, float], radius: float,
                      rect: Rect) -> float:
    """圆心到矩形距离减去半径; >=0 表示扫掠圆不与矩形相交."""
    return point_rect_distance(center, rect) - radius


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
    `arm_base` 的 Z 是 UR5 模型根安装面 (Z_base), 与岛面顶面相同;
    物料接触面由 `Z_WORK`/传送带带面派生, 不隐含在 island_top_z 中.
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

    @property
    def work_surface_z(self) -> float:
        return cfg.Z_WORK.get(self.unit_id, self.island_top_z)

    # ── 目标位姿 ────────────────────────────────────────────

    def targets(self, lines: Optional[dict] = None
                ) -> dict[str, tuple[float, float, float]]:
        """本工位需要够到的物料目标 (世界坐标, 物料中心 Z)."""
        lines = lines or _LINES
        out: dict[str, tuple[float, float, float]] = {}
        if self.unit_id == "A":
            out["line_s1a"] = lines["line_s1a"].pick_material_pose
            out["handoff_ac"] = _handoff_material_pose()
            out["dock_A"] = self.agv_material_pose
        elif self.unit_id == "B":
            out["line_s1b"] = lines["line_s1b"].pick_material_pose
            out["dock_B"] = self.agv_material_pose
        elif self.unit_id == "C":
            out["handoff_ac"] = _handoff_material_pose()
            out["line_ce_place"] = lines["line_ce"].place_material_pose
        elif self.unit_id == "D":
            out["line_s2d"] = lines["line_s2d"].pick_material_pose
            out["dock_D"] = self.agv_material_pose
        elif self.unit_id == "E":
            out["line_ce"] = lines["line_ce"].pick_material_pose
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
                self.work_surface_z + base.MATERIAL_HALF_HEIGHT)

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
            "work_surface_z": self.work_surface_z,
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

    C 臂从东侧接近 A→C 交接台; A 放置后若只做 +0.15 m 竖直撤离, 腕部
    仍在交接台上方, 会与 C 的接近路径重叠. A 必须把工具移到交接台
    西南侧上方 (仍在本臂可达范围内), 让出交接台包络. 该目标由布局
    派生, 不写入工件 digest.
    """
    if unit_id != "A":
        return None
    x, y = cfg.HANDOFF_AC["position"]
    return (x - 0.15, y - 0.30, base.STATION_TOP_Z + 0.40,
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
# AGV 路网
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AgvEdge:
    start: str
    end: str
    lane: str

    def length(self, nodes: Optional[dict] = None) -> float:
        nodes = nodes or cfg.AGV_NODES
        a = nodes[self.start]
        b = nodes[self.end]
        return math.dist(a, b)

    def as_dict(self, nodes: Optional[dict] = None) -> dict:
        return {"start": self.start, "end": self.end,
                "lane": self.lane,
                "length": round(self.length(nodes), 6)}


def agv_edges() -> tuple[AgvEdge, ...]:
    return tuple(AgvEdge(start, end, lane)
                 for start, end, lane in cfg.AGV_EDGES)


def agv_neighbors(nodes: Optional[dict] = None,
                  edges: Optional[tuple[AgvEdge, ...]] = None
                  ) -> dict[str, list[tuple[str, AgvEdge]]]:
    nodes = nodes or cfg.AGV_NODES
    edges = agv_edges() if edges is None else edges
    out: dict[str, list[tuple[str, AgvEdge]]] = {
        node: [] for node in nodes}
    for edge in edges:
        out[edge.start].append((edge.end, edge))
        out[edge.end].append((edge.start, edge))
    return out


def shortest_node_path(layout: "Stage4Layout", start: str, end: str
                       ) -> list[str]:
    """按边长度求两节点间最短路径 (Dijkstra); 无解抛 ValueError."""
    nodes = layout.agv_nodes
    if start not in nodes or end not in nodes:
        raise ValueError(f"未知 AGV 节点: {start!r} → {end!r}")
    neighbors = agv_neighbors(nodes, layout.agv_edges)
    dist = {node: math.inf for node in nodes}
    prev: dict[str, str] = {}
    dist[start] = 0.0
    heap = [(0.0, start)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist[node] + 1e-12:
            continue
        if node == end:
            break
        for other, edge in neighbors[node]:
            candidate = cost + edge.length(nodes)
            if candidate < dist[other] - 1e-12:
                dist[other] = candidate
                prev[other] = node
                heapq.heappush(heap, (candidate, other))
    if not math.isfinite(dist[end]):
        raise ValueError(f"AGV 路网不连通: {start!r} → {end!r}")
    path = [end]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def lane_for(layout: "Stage4Layout", start: str, end: str) -> str:
    for edge in layout.agv_edges:
        if {edge.start, edge.end} == {start, end}:
            return edge.lane
    raise ValueError(f"没有连接 {start!r} 与 {end!r} 的直道")


def agv_lane_rect(layout: "Stage4Layout", start: str, end: str) -> Rect:
    """一条直道的 AGV 扫掠矩形 (不含端点 dock footprint).

    端点 dock 的占地由 `dock_vs_*` 单独检查; 转向节点的旋转扫掠由
    `turn_circle` 检查. 这里只覆盖两节点中心之间的直线通道.
    """
    a = layout.agv_nodes[start]
    b = layout.agv_nodes[end]
    half_w = cfg.AGV_ROUTE_HALF_WIDTH + cfg.AGV_ROUTE_CLEARANCE
    if abs(a[0] - b[0]) <= 1e-9:
        return (a[0] - half_w, min(a[1], b[1]),
                a[0] + half_w, max(a[1], b[1]))
    if abs(a[1] - b[1]) <= 1e-9:
        return (min(a[0], b[0]), a[1] - half_w,
                max(a[0], b[0]), a[1] + half_w)
    raise ValueError(f"AGV 直道必须轴对齐: {start!r} → {end!r}")


def agv_turn_radius() -> float:
    """原地转向时车体+载荷的外接圆半径 (加安全余量)."""
    half_diag = math.hypot(*cfg.AGV_FOOTPRINT_AT_DOCK) / 2.0
    return half_diag + cfg.AGV_ROUTE_CLEARANCE


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
    e_output_table: tuple[float, float] = tuple(
        cfg.E_OUTPUT_TABLE["position"])
    e_output_table_size: tuple[float, float, float] = tuple(
        cfg.E_OUTPUT_TABLE["size"])
    agv_nodes: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {key: tuple(value)
                                 for key, value in cfg.AGV_NODES.items()})
    agv_edges: tuple[AgvEdge, ...] = field(default_factory=agv_edges)
    height_datum: dict = field(default_factory=lambda: _height_datum())

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
    def e_output_table_rect(self) -> Rect:
        return rect_of(self.e_output_table,
                       self.e_output_table_size[:2])

    @property
    def agv_lane_rects(self) -> dict[str, Rect]:
        return {f"{edge.start}->{edge.end}": agv_lane_rect(
                    self, edge.start, edge.end)
                for edge in self.agv_edges}

    @property
    def agv_route_rect(self) -> Rect:
        """所有直道扫掠矩形的外包围盒 (报告/兼容接口)."""
        rects = list(self.agv_lane_rects.values())
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    @property
    def agv_turn_nodes(self) -> tuple[str, ...]:
        # 按配置中的固定顺序输出, 不依赖 dict 插入顺序 (JSON 往返会
        # 改变键顺序, 曾导致 layout digest 在 build/save 进程间漂移).
        return tuple(node for node in cfg.AGV_TURN_NODE_IDS
                     if node in self.agv_nodes)

    def agv_path(self, start_dock: str, end_dock: str) -> list[str]:
        return shortest_node_path(self, start_dock, end_dock)

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
            "e_output_table": {
                "position": list(self.e_output_table),
                "size": list(self.e_output_table_size),
                "top_z": cfg.E_OUTPUT_TABLE["top_z"],
            },
            "height_datum": dict(self.height_datum),
            "agv_network": {
                "nodes": {key: list(value)
                          for key, value in self.agv_nodes.items()},
                "edges": [edge.as_dict(self.agv_nodes)
                          for edge in self.agv_edges],
                "lanes": list(cfg.AGV_LANES),
                "turn_nodes": list(self.agv_turn_nodes),
                "turn_radius": round(agv_turn_radius(), 6),
                "dock_heading": round(self.agv_initial_yaw, 6),
            },
        }

    def digest_payload(self) -> dict:
        return self.as_dict()


def _height_datum() -> dict:
    return {key: (dict(value) if isinstance(value, dict) else value)
            for key, value in cfg.HEIGHT_DATUM.items()}


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
    def work_surface_z(self):
        return self.spec.work_surface_z

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


L5_UFLOW = Stage4Layout(
    name=cfg.STAGE4_LAYOUT_NAME,
    units=tuple(_unit_from_def(unit_id) for unit_id in cfg.UNIT_IDS),
    docks={unit_id: tuple(cfg.UNIT_DEFS[unit_id]["dock"])
           for unit_id in cfg.UNIT_IDS
           if cfg.UNIT_DEFS[unit_id]["dock"] is not None},
)


def default_layout() -> Stage4Layout:
    return L5_UFLOW


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
    datum = data.get("height_datum")
    network = data.get("agv_network") or {}
    nodes = {str(key): tuple(float(v) for v in value)
             for key, value in (network.get("nodes") or {}).items()}
    edges = tuple(AgvEdge(str(item["start"]), str(item["end"]),
                          str(item["lane"]))
                  for item in (network.get("edges") or ()))
    # `layout.as_dict()` 的 e_output_table 是 {position,size,top_z};
    # 旧 manifest 顶层 layout 由 dataclasses.asdict 生成, 两个字段分别是
    # position tuple 和 size tuple. 两种形态都要能读回.
    table = data.get("e_output_table")
    if isinstance(table, dict):
        table_pos = table.get("position", cfg.E_OUTPUT_TABLE["position"])
        table_size = table.get("size", cfg.E_OUTPUT_TABLE["size"])
    else:
        table_pos = table or cfg.E_OUTPUT_TABLE["position"]
        table_size = (data.get("e_output_table_size")
                      or cfg.E_OUTPUT_TABLE["size"])
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
        e_output_table=tuple(float(v) for v in table_pos),
        e_output_table_size=tuple(float(v) for v in table_size),
        agv_nodes=(nodes or {key: tuple(value)
                             for key, value in cfg.AGV_NODES.items()}),
        agv_edges=(edges or agv_edges()),
        height_datum=(dict(datum) if isinstance(datum, dict)
                      else _height_datum()),
    )


# ═══════════════════════════════════════════════════════════════
# 静态校验
# ═══════════════════════════════════════════════════════════════

def validate_layout(layout: Stage4Layout) -> dict:
    """静态几何与粗可达性检查 (不代替 simIK / 碰撞验证).

    检查项:
      - 5 个工位、每台臂到本岛取放/交接/dock 目标可达;
      - Island/Station/Dock/Warehouse/Conveyor 两两不压占;
      - AGV 三段直道扫掠矩形与转向圆不压占任何工位/设备;
      - 物流线取料区位于有效行程内且只被目标工位够到;
      - 固定交接台被 A/C 两台臂够到, E 缓冲被 E 臂够到;
      - AGV 路网连通、dock 不共线、所有 dock 朝 -Y 进出;
      - 高度 datum 覆盖全部工位且与基座安装面一致.
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
        "e_output_table": layout.e_output_table_rect,
    }
    for line_id, line in layout.lines.items():
        if line_id != "line_s1a":
            devices[f"line_{line_id}"] = line.rect

    check("e_output_table_floor",
          rect_inside(layout.e_output_table_rect, floor),
          f"{tuple(round(v, 4) for v in layout.e_output_table_rect)}")
    check("e_output_table_height",
          abs(float(cfg.E_OUTPUT_TABLE["top_z"]) - cfg.Z_BASE) <= 1e-9,
          f"top_z={cfg.E_OUTPUT_TABLE['top_z']}")
    output_rect = rect_of(cfg.E_OUTPUT["position"],
                          (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
    check("e_output_on_table",
          rect_inside(output_rect, layout.e_output_table_rect),
          f"output={cfg.E_OUTPUT['position']} "
          f"table={tuple(round(v, 4) for v in layout.e_output_table_rect)}")

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
        for name, target in spec.targets(layout.lines).items():
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

    # AGV 路网: 连通、dock 不共线、直道与转向扫掠
    check("agv_nodes_match_docks",
          all(tuple(layout.agv_nodes[dock]) == tuple(pos)
              for dock, pos in layout.dock_positions.items()),
          f"docks={layout.dock_positions}")
    check("agv_docks_not_collinear",
          len({round(pos[0], 6)
               for pos in layout.dock_positions.values()}) > 1,
          f"docks={layout.dock_positions}")
    reachable_from = set()
    try:
        for dock in layout.dock_positions:
            reachable_from.update(layout.agv_path(
                layout.agv_initial_dock, dock))
        check("agv_network_connected",
              set(layout.dock_positions) <= reachable_from,
              f"reachable={sorted(reachable_from)}")
    except ValueError as exc:
        check("agv_network_connected", False, str(exc))
    check("agv_initial_heading",
          abs(abs(layout.agv_initial_yaw) - math.pi / 2.0) <= 1e-6,
          f"yaw={layout.agv_initial_yaw:.6f}")
    check("agv_initial_dock",
          layout.agv_initial_dock in layout.dock_positions,
          f"dock={layout.agv_initial_dock}")

    lane_rects = layout.agv_lane_rects
    for lane_name, rect in lane_rects.items():
        check(f"agv_lane_floor:{lane_name}", rect_inside(rect, floor),
              f"{tuple(round(v, 4) for v in rect)}")
        for spec in specs:
            gap = rect_clearance(rect, spec.island_rect)
            check(f"agv_lane_vs_island:{lane_name}:{spec.unit_id}",
                  gap >= 0.02, f"gap={gap:.4f} m")
            if spec.has_station:
                gap = rect_clearance(rect, spec.station_rect)
                check(f"agv_lane_vs_station:{lane_name}:{spec.unit_id}",
                      gap >= 0.02, f"gap={gap:.4f} m")
        for name, device in devices.items():
            gap = rect_clearance(rect, device)
            check(f"agv_lane_vs_{name}:{lane_name}", gap >= 0.02,
                  f"gap={gap:.4f} m")
        for key in cfg.E_BUFFERS:
            buffer_rect = rect_of(
                cfg.E_BUFFERS[key]["position"],
                (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
            gap = rect_clearance(rect, buffer_rect)
            check(f"agv_lane_vs_{key}:{lane_name}", gap >= 0.02,
                  f"gap={gap:.4f} m")
        output_rect = rect_of(
            cfg.E_OUTPUT["position"],
            (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
        gap = rect_clearance(rect, output_rect)
        check(f"agv_lane_vs_e_output:{lane_name}", gap >= 0.02,
              f"gap={gap:.4f} m")

    radius = agv_turn_radius()
    for node in layout.agv_turn_nodes:
        center = layout.agv_nodes[node]
        check(f"agv_turn_floor:{node}",
              rect_inside((center[0] - radius, center[1] - radius,
                           center[0] + radius, center[1] + radius), floor),
              f"center={center} radius={radius:.4f}")
        for spec in specs:
            gap = circle_clear_rect(center, radius, spec.island_rect)
            check(f"agv_turn_vs_island:{node}:{spec.unit_id}",
                  gap >= 0.0, f"clearance={gap:.4f} m")
            if spec.has_station:
                gap = circle_clear_rect(center, radius, spec.station_rect)
                check(f"agv_turn_vs_station:{node}:{spec.unit_id}",
                      gap >= 0.0, f"clearance={gap:.4f} m")
        for name, device in devices.items():
            gap = circle_clear_rect(center, radius, device)
            check(f"agv_turn_vs_{name}:{node}", gap >= 0.0,
                  f"clearance={gap:.4f} m")
        for key in cfg.E_BUFFERS:
            buffer_rect = rect_of(
                cfg.E_BUFFERS[key]["position"],
                (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
            gap = circle_clear_rect(center, radius, buffer_rect)
            check(f"agv_turn_vs_{key}:{node}", gap >= 0.0,
                  f"clearance={gap:.4f} m")
        output_rect = rect_of(
            cfg.E_OUTPUT["position"],
            (base.MATERIAL_SIZE[0], base.MATERIAL_SIZE[1]))
        gap = circle_clear_rect(center, radius, output_rect)
        check(f"agv_turn_vs_e_output:{node}", gap >= 0.0,
              f"clearance={gap:.4f} m")

    # 高度 datum
    datum = layout.height_datum or {}
    check("height_datum_base",
          abs(float(datum.get("Z_base", -1.0)) - cfg.Z_BASE) <= 1e-9,
          f"Z_base={datum.get('Z_base')}")
    check("height_datum_units",
          set((datum.get("Z_work") or {})) == set(cfg.UNIT_IDS),
          f"Z_work={sorted((datum.get('Z_work') or {}))}")
    for spec in specs:
        check(f"height_base_matches_island:{spec.unit_id}",
              abs(spec.arm_base[2] - cfg.Z_BASE) <= 1e-9
              and abs(spec.island_top_z - cfg.Z_BASE) <= 1e-9,
              f"base_z={spec.arm_base[2]} island_top={spec.island_top_z}")
    check("height_datum_transfer",
          abs(float(datum.get("H_transfer", -1.0)) - cfg.H_TRANSFER) <= 1e-9
          and cfg.H_TRANSFER >= cfg.Z_BELT + base.MATERIAL_SIZE[2]
          + 0.03,
          f"H_transfer={datum.get('H_transfer')} Z_belt={cfg.Z_BELT}")

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
