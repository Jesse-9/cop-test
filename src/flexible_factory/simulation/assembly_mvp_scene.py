"""assembly_mvp_scene.py — 混线装配 MVP 的两个独立岛场景扩展.

本模块只负责 ***assembly_mvp*** profile 的展示/落点几何:

  - 在两个独立岛上创建静态、非响应岛面、来料槽、逻辑装配位和成品位;
  - 提供 zone → 物料落点、来源生成位置和 alias 绑定;
  - 在已加载场景上做只读扩展校验 (alias 唯一、坐标/尺寸、与 v1
    工位/物流线/AGV 通道不重叠).

边界 (方案 §3.3/§4):
  - 不新增机器人、传送带、传感器、AGV 或运动对象;
  - 不修改 ``simulation/config.py`` / ``layout.py``, 不改变 v1
    ``config_digest`` / ``layout_digest`` 或受保护工件;
  - 场景对象全部静态且不可碰撞, 只作业务可视标记和物料落点.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from ..domain import assembly_mvp as mvp
from . import config as cfg
from . import config_base as base
from . import physics as phys
from .layout import default_layout
from .layout_base import rect_inside, rect_of
from .scene_base import (SceneBuildError, _create_box, _shape_size,
                          alias_index, alias_of, all_objects)

if TYPE_CHECKING:                       # pragma: no cover - 仅类型提示
    from .scene import Stage4Scene

PROFILE = mvp.PROFILE
FULL_FLOW_PROFILE = mvp.SCENE_PROFILE_FULL_FLOW
FLOOR_RECT = (-4.0, -3.0, 4.0, 3.0)
FLOOR_BOUNDS = FLOOR_RECT

ISLAND_HEIGHT = 0.02
TRAY_SIZE = (0.16, 0.12, 0.03)
PAD_SIZE = (0.24, 0.24, 0.02)
TRAY_CENTER_Z = ISLAND_HEIGHT + TRAY_SIZE[2] / 2.0
PAD_CENTER_Z = ISLAND_HEIGHT + PAD_SIZE[2] / 2.0
MATERIAL_Z = (ISLAND_HEIGHT + TRAY_SIZE[2]
              + base.MATERIAL_HALF_HEIGHT + base.MATERIAL_SPAWN_GAP)
MARK_Z = ISLAND_HEIGHT + PAD_SIZE[2]

ISLANDS = {
    "compressor": {
        "alias": "MVP_Island_COMPRESSOR",
        "center": (-2.55, 2.12),
        "size": (2.40, 1.25, ISLAND_HEIGHT),
        "color": (0.20, 0.30, 0.45),
    },
    "flange": {
        "alias": "MVP_Island_FLANGE",
        "center": (-2.55, -2.12),
        "size": (2.40, 1.25, ISLAND_HEIGHT),
        "color": (0.45, 0.30, 0.20),
    },
}


def _zone(alias: str, xy: tuple[float, float], kind: str, island: str,
          item_type: str = "", z: float = MATERIAL_Z) -> dict:
    spec = {"alias": alias, "xy": xy, "kind": kind, "island": island,
            "item_type": item_type, "z": float(z)}
    if kind in ("source", "output"):
        spec["tray_alias"] = f"{alias}_Tray"
        spec["tray_size"] = TRAY_SIZE
    elif kind == "assembly":
        spec["pad_alias"] = f"{alias}_Pad"
        spec["pad_size"] = PAD_SIZE
    return spec


ZONES = {
    mvp.ZONE_SOURCE_COMPRESSOR: _zone(
        "MVP_Source_COMPRESSOR", (-3.50, 2.38), "source", "compressor",
        mvp.ITEM_TYPE_COMPRESSOR),
    mvp.ZONE_SOURCE_BASEPLATE: _zone(
        "MVP_Source_BASEPLATE", (-3.50, 1.86), "source", "compressor",
        mvp.ITEM_TYPE_BASEPLATE),
    mvp.ZONE_COMPRESSOR_ASSEMBLY: _zone(
        "MVP_Compressor_Assembly", (-2.55, 2.12), "assembly", "compressor",
        z=MARK_Z),
    mvp.ZONE_OUTPUT_COMPRESSOR: _zone(
        "MVP_Output_COMPRESSOR_ASSY", (-1.60, 2.12), "output", "compressor",
        mvp.ITEM_TYPE_COMPRESSOR_ASSY),
    mvp.ZONE_SOURCE_FRONT_FLANGE: _zone(
        "MVP_Source_FRONT_FLANGE", (-3.50, -1.86), "source", "flange",
        mvp.ITEM_TYPE_FRONT_FLANGE),
    mvp.ZONE_SOURCE_REAR_FLANGE: _zone(
        "MVP_Source_REAR_FLANGE", (-3.50, -2.38), "source", "flange",
        mvp.ITEM_TYPE_REAR_FLANGE),
    mvp.ZONE_FLANGE_ASSEMBLY: _zone(
        "MVP_Flange_Assembly", (-2.55, -2.12), "assembly", "flange",
        z=MARK_Z),
    mvp.ZONE_OUTPUT_FLANGE: _zone(
        "MVP_Output_FLANGE_ASSY", (-1.60, -2.12), "output", "flange",
        mvp.ITEM_TYPE_FLANGE_ASSY),
}


def _extension_aliases() -> tuple[str, ...]:
    rows: list[str] = []
    for island in ISLANDS.values():
        rows.append(island["alias"])
    for zone in ZONES.values():
        rows.append(zone["alias"])
        for key in ("tray_alias", "pad_alias"):
            if key in zone:
                rows.append(zone[key])
    return tuple(rows)


ALIASES = _extension_aliases()


# ═══════════════════════════════════════════════════════════════
# 查询接口 (coordinator / lifecycle / contract 共用)
# ═══════════════════════════════════════════════════════════════

def order_sources() -> dict[str, dict]:
    """订单来源 zone → 生成位置/类型; 只包含 4 个来料槽."""
    out: dict[str, dict] = {}
    for zone_id in mvp.SOURCE_ZONES:
        spec = ZONES[zone_id]
        out[zone_id] = {
            "line": zone_id,
            "position": (spec["xy"][0], spec["xy"][1], MATERIAL_Z),
            "item_type": spec["item_type"],
            "alias": spec["alias"],
        }
    return out


def material_pose(zone: str) -> Optional[tuple[float, float, float]]:
    """zone 的物料落点; 逻辑装配位不是存储区, 返回 None."""
    spec = ZONES.get(str(zone))
    if spec is None or spec["kind"] == "assembly":
        return None
    return (spec["xy"][0], spec["xy"][1], spec["z"])


def extension_present(sim) -> bool:
    index = alias_index(sim)
    return any(alias in index for alias in ALIASES)


def profile_of(sim, scene=None) -> str:
    if scene is not None:
        # build/adopt 路径总是写入 scene_profile; 未写入的测试替身按
        # full_flow 处理, 避免额外的全场景枚举.
        return str(getattr(scene, "scene_profile", "")
                   or FULL_FLOW_PROFILE)
    return PROFILE if extension_present(sim) else FULL_FLOW_PROFILE


def contract_payload() -> dict:
    """写入 manifest extra 的稳定扩展契约 (只含静态业务数据)."""
    return {
        "profile": PROFILE,
        "extension_version": mvp.EXTENSION_VERSION,
        "islands": [
            {"id": island_id, "alias": spec["alias"],
             "center": [round(float(value), 4)
                        for value in spec["center"]],
             "size": [round(float(value), 4) for value in spec["size"]]}
            for island_id, spec in ISLANDS.items()],
        "zones": [
            {"zone_id": zone_id, "alias": spec["alias"],
             "kind": spec["kind"], "island": spec["island"],
             "item_type": spec["item_type"],
             "position": [round(float(spec["xy"][0]), 4),
                          round(float(spec["xy"][1]), 4),
                          round(float(spec["z"]), 4)]}
            for zone_id, spec in ZONES.items()],
        "join_templates": [
            {"recipe_id": recipe_id,
             "template_id": product["template_id"],
             "handler_id": mvp.JOIN_HANDLER,
             "inputs": [
                 {"zone": zone, "item_type": item_type,
                  "port_id": port_id}
                 for zone, item_type, port_id in product["inputs"]],
             "output": {"zone": product["output"][0],
                        "item_type": product["output"][1],
                        "port_id": product["output"][2]}}
            for recipe_id, product in mvp.PRODUCTS.items()],
    }


# ═══════════════════════════════════════════════════════════════
# 构建 / 绑定
# ═══════════════════════════════════════════════════════════════

def _create_dummy(sim, alias: str, position, parent: int) -> int:
    handle = int(sim.createDummy(0.01))
    sim.setObjectAlias(handle, alias)
    if parent >= 0:
        sim.setObjectParent(handle, parent, True)
    sim.setObjectPosition(handle, [float(value) for value in position],
                          sim.handle_world)
    return handle


def _create_static_box(sim, alias: str, size, world_pos, color,
                       parent: int) -> int:
    handle = _create_box(sim, alias, size, world_pos, color=color,
                         parent=parent, static=True)
    # 方案 §3.3: 辅助几何体静态且不可碰撞, 不改变现有运动/碰撞基线.
    phys.set_respondable(sim, handle, False)
    return handle


def build_extension(sim, scene) -> dict:
    """在已停止的 L5_UFLOW 场景上创建 MVP 扩展对象并绑定 alias."""
    root = int(scene.handle(base.ALIASES["roots"][2])) if scene is not None \
        else -1
    if root < 0:
        raise SceneBuildError("场景缺少 Island 根, 无法构建 MVP 扩展")
    if extension_present(sim):
        raise SceneBuildError("场景已包含 MVP 扩展对象, 拒绝重复构建")
    try:
        for island in ISLANDS.values():
            center = island["center"]
            size = island["size"]
            _create_static_box(
                sim, island["alias"], size,
                (center[0], center[1], size[2] / 2.0),
                island["color"], root)
        for zone in ZONES.values():
            _create_dummy(sim, zone["alias"],
                          (zone["xy"][0], zone["xy"][1], zone["z"]), root)
            if "tray_alias" in zone:
                color = mvp.ITEM_COLORS.get(zone["item_type"],
                                            (0.70, 0.70, 0.70))
                _create_static_box(
                    sim, zone["tray_alias"], zone["tray_size"],
                    (zone["xy"][0], zone["xy"][1], TRAY_CENTER_Z),
                    color, root)
            if "pad_alias" in zone:
                _create_static_box(
                    sim, zone["pad_alias"], zone["pad_size"],
                    (zone["xy"][0], zone["xy"][1], PAD_CENTER_Z),
                    (0.62, 0.66, 0.72), root)
        info = bind_extension(sim, scene)
    except Exception:
        _remove_extension_objects(sim)
        raise
    return info


def bind_extension(sim, scene: Optional["Stage4Scene"] = None,
                   index: Optional[dict] = None) -> Optional[dict]:
    """把已存在的扩展 alias 绑定到 scene.handles; 无扩展返回 None."""
    index = alias_index(sim) if index is None else index
    handles: dict[str, int] = {}
    missing: list[str] = []
    duplicated: list[str] = []
    for alias in ALIASES:
        hits = index.get(alias, [])
        if len(hits) == 1:
            handles[alias] = int(hits[0])
        elif not hits:
            missing.append(alias)
        else:
            duplicated.append(alias)
    if missing and len(missing) == len(ALIASES):
        return None
    if missing or duplicated:
        raise SceneBuildError(
            f"MVP 扩展 alias 不完整: missing={missing} "
            f"duplicated={duplicated}")
    if scene is not None:
        scene.scene_profile = PROFILE
        for alias, handle in handles.items():
            scene.handles[alias] = handle
    return {
        "profile": PROFILE,
        "extension_version": mvp.EXTENSION_VERSION,
        "handles": dict(handles),
        "zones": {
            zone_id: {"alias": spec["alias"],
                      "handle": handles[spec["alias"]],
                      "position": list(material_pose(zone_id)
                                       or (spec["xy"][0], spec["xy"][1],
                                           spec["z"]))}
            for zone_id, spec in ZONES.items()},
        "islands": {
            island_id: {"alias": spec["alias"],
                        "handle": handles[spec["alias"]]}
            for island_id, spec in ISLANDS.items()},
    }


def _remove_extension_objects(sim) -> None:
    aliases = set(ALIASES)
    for _ in range(3):
        pending = [handle for handle in all_objects(sim)
                   if alias_of(sim, handle) in aliases]
        if not pending:
            break
        try:
            sim.removeObjects(pending)
        except Exception:
            for handle in pending:
                try:
                    sim.removeObjects([handle])
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════
# 只读校验
# ═══════════════════════════════════════════════════════════════

def _v1_rects(layout) -> list[tuple[str, tuple[float, float, float, float]]]:
    rows: list[tuple[str, tuple[float, float, float, float]]] = []
    for unit in layout.units:
        rows.append((f"unit_{unit.unit_id}_island", unit.island_rect))
        rows.append((f"unit_{unit.unit_id}_station", unit.station_rect))
        if unit.dock_rect is not None:
            rows.append((f"unit_{unit.unit_id}_dock", unit.dock_rect))
    for line_id, line in layout.lines.items():
        rows.append((f"line_{line_id}", line.rect))
    for lane_id, rect in layout.agv_lane_rects.items():
        rows.append((f"agv_lane_{lane_id}", rect))
    rows.append(("warehouse_s2",
                 rect_of(cfg.WAREHOUSE_S2_POS,
                         cfg.WAREHOUSE_S2_SIZE[:2])))
    rows.append(("e_output_table",
                 rect_of(cfg.E_OUTPUT_TABLE["position"],
                         cfg.E_OUTPUT_TABLE["size"][:2])))
    return rows


def _rect_clearance(a, b) -> float:
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    return max(dx, dy)


def verify_extension(sim, scene=None) -> list[dict]:
    """MVP 扩展只读契约; 返回与 scene.verify() 相同的 check 行."""
    checks: list[dict] = []
    layout = (getattr(scene, "stage4_layout", None)
              if scene is not None else None) or default_layout()

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    index = alias_index(sim)
    for alias in ALIASES:
        hits = index.get(alias, [])
        check(f"mvp_alias_unique:{alias}", len(hits) == 1,
              f"{len(hits)} 个匹配")
        check(f"mvp_alias_flat:{alias}", "/" not in alias, alias)

    for island_id, spec in ISLANDS.items():
        hits = index.get(spec["alias"], [])
        if len(hits) != 1:
            continue
        handle = hits[0]
        pos = list(sim.getObjectPosition(handle, sim.handle_world))
        expected = (spec["center"][0], spec["center"][1],
                    spec["size"][2] / 2.0)
        check(f"mvp_island_position:{island_id}",
              all(abs(pos[index_] - expected[index_]) <= 0.005
                  for index_ in range(3)),
              f"pos={[round(float(v), 4) for v in pos]}")
        size = _shape_size(sim, handle)
        check(f"mvp_island_size:{island_id}",
              size is not None
              and all(abs(size[index_] - spec["size"][index_]) <= 0.002
                      for index_ in range(3)),
              f"size={size}")
        _check_static_shape(sim, handle, f"mvp_island:{island_id}", check)

    for zone_id, spec in ZONES.items():
        hits = index.get(spec["alias"], [])
        if len(hits) != 1:
            continue
        handle = hits[0]
        pos = list(sim.getObjectPosition(handle, sim.handle_world))
        expected = (spec["xy"][0], spec["xy"][1], spec["z"])
        check(f"mvp_zone_position:{zone_id}",
              all(abs(pos[index_] - expected[index_]) <= 0.005
                  for index_ in range(3)),
              f"pos={[round(float(v), 4) for v in pos]}")
        attachment = spec.get("tray_alias") or spec.get("pad_alias")
        if attachment:
            rows = index.get(attachment, [])
            if len(rows) == 1:
                _check_static_shape(sim, rows[0],
                                    f"mvp_zone_attachment:{zone_id}", check)
            else:
                check(f"mvp_zone_attachment:{zone_id}", False,
                      f"{attachment} 匹配 {len(rows)} 个")

    # 几何: 岛面边界、槽位/装配位不重叠, 且不与 v1 静态/运动对象相交.
    island_rects = {
        island_id: rect_of(spec["center"], spec["size"][:2])
        for island_id, spec in ISLANDS.items()}
    for island_id, rect in island_rects.items():
        check(f"mvp_island_in_floor:{island_id}",
              rect_inside(rect, FLOOR_RECT),
              f"rect={rect} floor={FLOOR_RECT}")
    for name, rect in _v1_rects(layout):
        for island_id, island_rect in island_rects.items():
            clearance = _rect_clearance(island_rect, rect)
            check(f"mvp_island_clear:{island_id}:{name}",
                  clearance >= -1e-6,
                  f"clearance={round(float(clearance), 5)}")

    zone_rects: list[tuple[str, tuple[float, float, float, float]]] = []
    for zone_id, spec in ZONES.items():
        attachment = spec.get("tray_size") or spec.get("pad_size")
        if attachment is None:
            continue
        rect = rect_of(spec["xy"], attachment[:2])
        zone_rects.append((zone_id, rect))
        island_rect = island_rects[spec["island"]]
        check(f"mvp_zone_in_island:{zone_id}",
              rect_inside(rect, island_rect),
              f"rect={rect} island={island_rect}")
        check(f"mvp_zone_in_floor:{zone_id}",
              rect_inside(rect, FLOOR_RECT), f"rect={rect}")
    for first in range(len(zone_rects)):
        for second in range(first + 1, len(zone_rects)):
            clearance = _rect_clearance(zone_rects[first][1],
                                        zone_rects[second][1])
            check(f"mvp_zone_clear:{zone_rects[first][0]}:"
                  f"{zone_rects[second][0]}", clearance >= -1e-6,
                  f"clearance={round(float(clearance), 5)}")
    return checks


def _check_static_shape(sim, handle: int, name: str, check) -> None:
    try:
        static = int(sim.getObjectInt32Param(
            handle, sim.shapeintparam_static))
        respondable = int(sim.getObjectInt32Param(
            handle, sim.shapeintparam_respondable))
    except Exception as exc:
        check(name, False, f"{type(exc).__name__}: {exc}")
        return
    check(name, static == 1 and respondable == 0,
          f"static={static} respondable={respondable}")


def extension_contract_mismatches(saved: Optional[dict]) -> list[str]:
    """核对 manifest 保存的 MVP 契约与当前静态定义是否一致."""
    current = contract_payload()
    if not isinstance(saved, dict):
        return ["missing assembly_mvp contract"]
    errors: list[str] = []
    for key in ("profile", "extension_version"):
        if saved.get(key) != current[key]:
            errors.append(f"{key}: saved={saved.get(key)!r} "
                          f"current={current[key]!r}")
    for key in ("islands", "zones", "join_templates"):
        if saved.get(key) != current[key]:
            errors.append(f"{key} 与当前 MVP 定义不一致")
    return errors


__all__ = [
    "ALIASES", "FLOOR_BOUNDS", "FLOOR_RECT", "FULL_FLOW_PROFILE", "ISLANDS",
    "MATERIAL_Z", "PROFILE", "ZONES", "bind_extension", "build_extension",
    "contract_payload", "extension_contract_mismatches", "extension_present",
    "material_pose", "order_sources", "profile_of", "verify_extension",
]