"""layout_base.py — v1 基础层布局描述与几何校验 (L1_S1).

`LayoutSpec` 把机械臂基座、岛体、置物台和 AGV 装卸点集中为一个不可变对象。
v1 基础层场景构建器 (`scene_base.build_scene`) 只读取该对象, 不修改任何模块级常量。

当前生产布局是步骤 1.1 选定的 **L1_S1**: UR5 侧装于传送带 +Y 侧,
置物台位于机械臂后方, AGV 装卸点与机械臂同侧; 场景不设挡停体。
`include_stopper` 仅为步骤 1.1 A 组历史证据保留的元数据, 当前构建器不再
创建挡停体。

坐标约定: X = 输送方向, Y = 横向, Z = 高度; 单位 m / rad。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import config_base as cfg


# ═══════════════════════════════════════════════════════════════
# 平面几何工具 (XY)
# ═══════════════════════════════════════════════════════════════

Rect = tuple[float, float, float, float]   # (min_x, min_y, max_x, max_y)


def rect_of(center_xy, size_xy) -> Rect:
    """以中心点和 (宽, 高) 生成 XY 平面矩形。"""
    return (float(center_xy[0]) - float(size_xy[0]) / 2.0,
            float(center_xy[1]) - float(size_xy[1]) / 2.0,
            float(center_xy[0]) + float(size_xy[0]) / 2.0,
            float(center_xy[1]) + float(size_xy[1]) / 2.0)


def overlap_rects(a: Rect, b: Rect, tol: float = 1e-6) -> bool:
    """两个 XY 矩形是否有正面积重叠; 边界相切不算压占。"""
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol
                or a[3] <= b[1] + tol or b[3] <= a[1] + tol)


def rect_inside(inner: Rect, outer: Rect, tol: float = 1e-6) -> bool:
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


# ═══════════════════════════════════════════════════════════════
# 布局描述
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LayoutSpec:
    """一个可构建的单工位布局。

    所有坐标均为世界坐标。传送带轴向和有效输送区域由
    `conveyor_center` 与 `pick_sensor_local_x` 决定; 候选布局只允许
    改变机械臂/岛体/置物台/AGV 的位置和挡停体开关。
    """

    name: str
    arm_base: tuple[float, float, float]
    island_pos: tuple[float, float]
    station_pos: tuple[float, float]
    agv_dock: tuple[float, float]
    conveyor_center: tuple[float, float] = tuple(cfg.CONVEYOR_CENTER)
    # 历史步骤 1.1 A 组的挡停体标记; 当前 v1 基础层构建器不再创建挡停体。
    include_stopper: bool = False
    pick_sensor_local_x: float = cfg.CONVEYOR_STOP_LOCAL_X
    warehouse_pos: tuple[float, float] = tuple(cfg.WAREHOUSE_POS)
    island_size: tuple[float, float, float] = tuple(cfg.ISLAND_SIZE)
    station_size: tuple[float, float, float] = tuple(cfg.STATION_SIZE)
    island_top_z: float = cfg.ISLAND_TOP_Z
    station_top_z: float = cfg.STATION_TOP_Z

    # ── 派生目标 ────────────────────────────────────────────

    @property
    def pick_material_pose(self) -> tuple[float, float, float]:
        """物料停在取料点时的中心 (与阶段一停止区 dummy 一致)。"""
        return (self.conveyor_center[0] + cfg.CONVEYOR_STOP_LOCAL_X,
                self.conveyor_center[1],
                cfg.CONVEYOR_SURFACE_Z + cfg.MATERIAL_HALF_HEIGHT)

    @property
    def pick_tcp_pose(self) -> list[float]:
        return cfg.tcp_pose_above(self.pick_material_pose)

    @property
    def station_material_pose(self) -> tuple[float, float, float]:
        return (self.station_pos[0], self.station_pos[1],
                self.station_top_z + cfg.MATERIAL_HALF_HEIGHT)

    @property
    def station_tcp_pose(self) -> list[float]:
        return cfg.tcp_pose_above(self.station_material_pose)

    @property
    def agv_material_pose(self) -> tuple[float, float, float]:
        return (self.agv_dock[0], self.agv_dock[1],
                cfg.AGV_TRAY_TOP_Z + cfg.MATERIAL_HALF_HEIGHT)

    @property
    def agv_tcp_pose(self) -> list[float]:
        return cfg.tcp_pose_above(self.agv_material_pose)

    def tcp_pose_for_target(self, target: str) -> list[float]:
        """放置目标 (station / agv) 对应的 TCP 世界位姿。"""
        if target == "station":
            return list(self.station_tcp_pose)
        if target == "agv":
            return list(self.agv_tcp_pose)
        raise ValueError(f"未知目标: {target!r} (可用: station / agv)")

    def material_pose_for_target(self, target: str) -> list[float]:
        """放置完成时物料中心的世界位姿 (含单位四元数)。"""
        if target == "station":
            return [*self.station_material_pose, 0.0, 0.0, 0.0, 1.0]
        if target == "agv":
            return [*self.agv_material_pose, 0.0, 0.0, 0.0, 1.0]
        raise ValueError(f"未知目标: {target!r} (可用: station / agv)")

    def targets(self) -> dict[str, list[float]]:
        """阶段一保留的三个 TCP 目标。"""
        return {
            "pick": list(self.pick_tcp_pose),
            "station": list(self.station_tcp_pose),
            "agv": list(self.agv_tcp_pose),
        }

    # ── 平面投影 ────────────────────────────────────────────

    @property
    def conveyor_rect(self) -> Rect:
        return rect_of(
            self.conveyor_center,
            (cfg.CONVEYOR_DESIGN_LENGTH, cfg.CONVEYOR_NATIVE_WIDTH))

    @property
    def island_rect(self) -> Rect:
        return rect_of(self.island_pos, self.island_size[:2])

    @property
    def station_rect(self) -> Rect:
        return rect_of(self.station_pos, self.station_size[:2])

    @property
    def agv_rect(self) -> Rect:
        return rect_of(self.agv_dock, cfg.AGV_FOOTPRINT_SIZE)

    @property
    def warehouse_rect(self) -> Rect:
        return rect_of(self.warehouse_pos, cfg.WAREHOUSE_SIZE[:2])

    @property
    def floor_rect(self) -> Rect:
        return rect_of(cfg.FLOOR_CENTER[:2], cfg.FLOOR_SIZE[:2])


# 当前生产布局: 步骤 1.1 选定结果 (2026-10-20)。
# 坐标来自 06-07 候选矩阵; 传送带轴向和取料传感器位置不变。
L1_S1_LAYOUT = LayoutSpec(
    name="L1_S1",
    arm_base=(-0.20, 0.55, cfg.ISLAND_TOP_Z),
    island_pos=(-0.20, 0.55),
    station_pos=(-0.20, 1.20),
    agv_dock=(0.40, 0.75),
    conveyor_center=tuple(cfg.CONVEYOR_CENTER),
    include_stopper=False,
    pick_sensor_local_x=cfg.CONVEYOR_STOP_LOCAL_X,
    warehouse_pos=tuple(cfg.WAREHOUSE_POS),
    island_size=tuple(cfg.ISLAND_SIZE),
    station_size=tuple(cfg.STATION_SIZE),
    island_top_z=cfg.ISLAND_TOP_Z,
    station_top_z=cfg.STATION_TOP_Z,
)


def default_layout() -> LayoutSpec:
    """当前生产布局 L1_S1; `build_scene()` 未显式传 layout 时使用。"""
    return L1_S1_LAYOUT


_TUPLE_FIELDS = {"arm_base", "island_pos", "station_pos", "agv_dock",
                 "conveyor_center", "warehouse_pos", "island_size",
                 "station_size"}


def layout_from_dict(data: dict) -> LayoutSpec:
    """从 manifest/JSON 恢复布局; 缺失字段使用 dataclass 默认值。"""
    if not isinstance(data, dict):
        raise ValueError("layout 必须是对象")
    import dataclasses
    names = {f.name for f in dataclasses.fields(LayoutSpec)}
    kwargs = {}
    for key, value in data.items():
        if key not in names or key == "name":
            continue
        if key in _TUPLE_FIELDS and isinstance(value, (list, tuple)):
            value = tuple(float(v) for v in value)
        kwargs[key] = value
    name = str(data.get("name") or data.get("id") or "")
    if not name:
        raise ValueError("layout 缺少 name/id")
    return LayoutSpec(name=name, **kwargs)


# ═══════════════════════════════════════════════════════════════
# 布局校验
# ═══════════════════════════════════════════════════════════════

def validate_layout(layout: LayoutSpec) -> dict:
    """静态几何与粗可达性检查 (不代替 simIK / 碰撞验证)。

    检查项:
      - 机械臂基座与岛体中心一致;
      - 岛体/置物台/AGV 车体不压占传送带、仓库或彼此;
      - 三件设备都在地面范围内;
      - 取料/工位/AGV 三个 TCP 目标在 UR5 可达余量内。

    返回 ``{"ok": bool, "checks": [{"check", "ok", "detail"}]}``。
    """
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("arm_base_on_island",
          math.isclose(layout.arm_base[0], layout.island_pos[0])
          and math.isclose(layout.arm_base[1], layout.island_pos[1])
          and math.isclose(layout.arm_base[2], layout.island_top_z),
          f"arm={layout.arm_base} island={layout.island_pos} "
          f"top_z={layout.island_top_z}")

    pairs = (
        ("island_vs_conveyor", layout.island_rect, layout.conveyor_rect),
        ("island_vs_warehouse", layout.island_rect, layout.warehouse_rect),
        ("station_vs_conveyor", layout.station_rect, layout.conveyor_rect),
        ("station_vs_island", layout.station_rect, layout.island_rect),
        ("station_vs_agv", layout.station_rect, layout.agv_rect),
        ("station_vs_warehouse", layout.station_rect, layout.warehouse_rect),
        ("agv_vs_conveyor", layout.agv_rect, layout.conveyor_rect),
        ("agv_vs_island", layout.agv_rect, layout.island_rect),
        ("agv_vs_station", layout.agv_rect, layout.station_rect),
        ("agv_vs_warehouse", layout.agv_rect, layout.warehouse_rect),
    )
    for name, a, b in pairs:
        check(name, not overlap_rects(a, b),
              f"{tuple(round(v, 4) for v in a)} vs "
              f"{tuple(round(v, 4) for v in b)}")

    floor = layout.floor_rect
    for name, rect in (("island", layout.island_rect),
                       ("station", layout.station_rect),
                       ("agv", layout.agv_rect)):
        check(f"floor_bounds:{name}", rect_inside(rect, floor),
              f"{tuple(round(v, 4) for v in rect)} in "
              f"{tuple(round(v, 4) for v in floor)}")

    for name, target in layout.targets().items():
        d = math.dist(layout.arm_base, target[:3])
        check(f"arm_reach:{name}", d <= cfg.REACH_LIMIT,
              f"{d:.3f} m <= {cfg.REACH_LIMIT:.3f} m")

    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "layout": layout.name}
