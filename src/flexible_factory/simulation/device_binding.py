"""device_binding.py — 阶段 2 设备 ID 与场景/运行时对象的静态绑定表.

本模块把 ``DeviceSpec.id``（工程稳定标识）映射到 CoppeliaSim 对象别名、
运行时组件索引键、资源表条目和区域 ID。它不持有句柄、不访问仿真 API、
不导入 ``application`` / ``cli``；只有 :func:`verify_scene_bindings` 在已
加载场景上解析别名，供 focused harness 与阶段 2 报告使用。

边界（06-20 实施顺序 1）：
  - 设备/能力/区域目录仍由 ``domain/process_model.py`` 与
    ``domain/default_process.py`` 定义，本表不重复声明能力；
  - 传送带、交接台、E 缓冲、成品输出台和传感器不是 ``DeviceSpec``，只在
    :func:`support_bindings` 中登记物理承载，用于资源/区域完整性核对；
  - 放置目标点（``UNIT_ALIASES[*]["output_target"]``）是同一对象在
    交接台/传送带/缓冲上的几何别名，由 support 绑定覆盖，臂绑定不重复登记；
  - 绑定表不写入 manifest、不参与 ``config_digest``；句柄只存在于场景与
    运行时对象中，本表只有字符串 ID。

用途与判据：
  - :func:`validate_bindings` 是纯校验（不需要仿真），核对目录覆盖、
    资源/区域闭合、别名唯一与工序可达；
  - :func:`verify_scene_bindings` 需要已加载场景，核对别名可解析、不同
    别名不指向同一对象、工位缓存句柄与别名解析一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..domain import rules as domain_rules
from ..domain.process_model import ProcessCatalog
from . import config as cfg
from . import config_base as base


class DeviceBindingError(RuntimeError):
    """绑定表缺失或非法: 未知设备、缺项、重复别名."""

    code = "V1_DEVICE_BINDING"


# 臂绑定登记的别名角色; 顺序即核对顺序.
# ``output_target`` 不在此列: 它与交接台/线入口/缓冲别名同一对象.
_ARM_ALIAS_ROLES = ("robot", "suction", "tcp", "suction_sensor",
                    "model_sensor", "ik_target", "island", "station")

# 工位缓存句柄与别名解析结果交叉核对的属性名.
_UNIT_HANDLE_ATTRS = {
    "robot": "robot_root",
    "suction": "suction_root",
    "tcp": "tcp",
    "suction_sensor": "suction_sensor",
    "model_sensor": "suction_model_sensor",
    "ik_target": "ik_target",
    "island": "island",
    "station": "station",
}

# 每类执行器需要的运行时组件索引键 (comp[...]).
_DEVICE_RUNTIME_KEYS = {
    "arm": ("runtime", "ik", "suction"),
    "agv": ("agv",),
    "logic": ("materials",),
}

# 每台臂: (默认线, 接触区域, 额外资源).
# 接触区域来自默认目录的输入/输出端口; 额外资源来自工序资源请求
# (交接台、dock、臂包络互锁区). 只登记默认工艺真正使用的条目.
_ARM_ROWS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "A": ("line_s1a",
          ("line_s1a", "handoff_ac", "agv"),
          ("handoff_ac", "dock_A", "zone_ab", "zone_ac")),
    "B": ("line_s1b",
          ("line_s1b", "agv"),
          ("dock_B", "zone_ab")),
    "C": ("line_ce",
          ("handoff_ac", "line_ce"),
          ("handoff_ac", "zone_ac")),
    "D": ("line_s2d",
          ("line_s2d", "agv"),
          ("dock_D",)),
    "E": ("line_ce",
          ("line_ce", "buffer_e_c", "buffer_e_d", "agv"),
          ("buffer_e_c", "buffer_e_d", "dock_E")),
}


# ═══════════════════════════════════════════════════════════════
# 绑定记录
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DeviceBinding:
    """一个设备的稳定标识到场景/运行时/资源/区域的对应关系."""

    device_id: str
    kind: str                       # arm | agv | logic
    aliases: tuple[str, ...]        # 场景对象别名 (不含 Shadow_*)
    runtime_keys: tuple[str, ...]   # 运行时组件索引键
    resources: tuple[str, ...]      # 资源表条目
    zones: tuple[str, ...]          # 物料区域 ID
    default_line_id: str = ""       # 仅机械臂: 控制器使用的默认线

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "runtime_keys": list(self.runtime_keys),
            "resources": list(self.resources),
            "zones": list(self.zones),
            "default_line_id": self.default_line_id,
        }


@dataclass(frozen=True)
class SupportBinding:
    """非 ``DeviceSpec`` 的物理承载: 传送带、交接台、缓冲、输出台."""

    id: str
    kind: str                       # line | station | buffer | output
    aliases: tuple[str, ...]
    resources: tuple[str, ...]
    zones: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "resources": list(self.resources),
            "zones": list(self.zones),
        }


# ═══════════════════════════════════════════════════════════════
# 绑定表
# ═══════════════════════════════════════════════════════════════

def _arm_binding(unit_id: str) -> DeviceBinding:
    aliases_cfg = cfg.UNIT_ALIASES[unit_id]
    default_line, zones, extra = _ARM_ROWS[unit_id]
    resources: list[str] = [f"arm_{unit_id}", default_line]
    for resource in extra:
        if resource not in resources:
            resources.append(resource)
    return DeviceBinding(
        device_id=f"arm_{unit_id}",
        kind="arm",
        aliases=tuple(aliases_cfg[role] for role in _ARM_ALIAS_ROLES),
        runtime_keys=_DEVICE_RUNTIME_KEYS["arm"],
        resources=tuple(resources),
        zones=tuple(zones),
        default_line_id=default_line)


def _agv_binding() -> DeviceBinding:
    dock_aliases = tuple(cfg.ALIASES["dock_sensor"][dock]
                         for dock in cfg.AGV_DOCK_IDS)
    aliases = (base.ALIASES["agv"], base.ALIASES["agv_tray"],
               base.ALIASES["agv_wall_front"],
               base.ALIASES["agv_wall_back"],
               base.ALIASES["agv_wall_left"],
               base.ALIASES["agv_wall_right"],
               *dock_aliases)
    resources = (*cfg.AGV_LANES,
                 *(f"dock_{dock}" for dock in cfg.AGV_DOCK_IDS))
    return DeviceBinding(
        device_id="agv_1",
        kind="agv",
        aliases=aliases,
        runtime_keys=_DEVICE_RUNTIME_KEYS["agv"],
        resources=resources,
        zones=("agv",))


def _logic_binding() -> DeviceBinding:
    """E 逻辑配对处理器: 无场景对象, 只有物料区域与谱系记录."""
    return DeviceBinding(
        device_id="logic_E",
        kind="logic",
        aliases=(),
        runtime_keys=_DEVICE_RUNTIME_KEYS["logic"],
        resources=(),
        zones=("buffer_e_c", "buffer_e_d", "output_e"))


def device_bindings() -> tuple[DeviceBinding, ...]:
    """全部设备的绑定; 每次返回新元组, 便于测试注入变体."""
    return (*(_arm_binding(unit_id) for unit_id in cfg.UNIT_IDS),
            _agv_binding(), _logic_binding())


def support_bindings() -> tuple[SupportBinding, ...]:
    """传送带/交接台/缓冲/输出台的物理承载; 用于资源与区域闭合核对."""
    rows: list[SupportBinding] = []
    for line_id, spec in cfg.LINE_DEFS.items():
        rows.append(SupportBinding(
            id=line_id, kind="line",
            aliases=(spec["conveyor"], spec["entry"], spec["stop"],
                     spec["sensor"]),
            resources=(line_id,), zones=(line_id,)))
    handoff = cfg.HANDOFF_AC
    # table_alias 是 A 工位台面, 已由 arm_A 的 station 绑定覆盖.
    rows.append(SupportBinding(
        id=handoff["id"], kind="station",
        aliases=(handoff["alias"], handoff["sensor"]),
        resources=(handoff["id"],), zones=(handoff["id"],)))
    for zone_id, spec in cfg.E_BUFFERS.items():
        rows.append(SupportBinding(
            id=zone_id, kind="buffer",
            aliases=(spec["alias"], spec["sensor"]),
            resources=(zone_id,), zones=(zone_id,)))
    rows.append(SupportBinding(
        id="output_e", kind="output",
        aliases=(cfg.E_OUTPUT["alias"], cfg.E_OUTPUT_TABLE["alias"]),
        resources=(), zones=("output_e",)))
    return tuple(rows)


def binding_for(device_id: str) -> DeviceBinding:
    """按设备 ID 取绑定; 未登记时抛 :class:`DeviceBindingError`."""
    for binding in device_bindings():
        if binding.device_id == device_id:
            return binding
    known = sorted(row.device_id for row in device_bindings())
    raise DeviceBindingError(
        f"设备 {device_id!r} 没有绑定; 已登记 {known}")


# ═══════════════════════════════════════════════════════════════
# 纯校验 (不需要仿真)
# ═══════════════════════════════════════════════════════════════

def _known_aliases() -> set[str]:
    """场景配置可解析的别名全集 (递归收集字符串值)."""
    aliases: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            aliases.add(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple, set)):
            for value in node:
                walk(value)

    walk(base.ALIASES)
    walk(cfg.ALIASES)
    walk(cfg.UNIT_ALIASES)
    return aliases


def _port_zones(operation) -> set[str]:
    zones = {port.zone for port in operation.inputs}
    if operation.output is not None:
        zones.add(operation.output.zone)
    return zones


def _row_id(row) -> str:
    """设备绑定用 ``device_id``, 承载绑定用 ``id``."""
    return row.device_id if isinstance(row, DeviceBinding) else row.id


def validate_bindings(catalog: ProcessCatalog) -> dict:
    """核对绑定表与工艺目录/资源表/场景配置的一致性.

    返回 ``{"ok", "errors", "checks", ...}``; 有错误时不抛异常, 由调用方决定
    是断言还是写报告 (:func:`binding_for` 等查询接口才抛)。
    """
    errors: list[str] = []
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def err(message: str) -> None:
        errors.append(message)

    bindings = device_bindings()
    supports = support_bindings()
    by_id: dict[str, DeviceBinding] = {}
    for binding in bindings:
        if binding.device_id in by_id:
            err(f"重复绑定: {binding.device_id!r}")
        by_id[binding.device_id] = binding

    # 1. 目录覆盖: 每个 DeviceSpec 恰好一个绑定, kind 一致.
    catalog_ids = [device.id for device in catalog.devices]
    uncovered = [device_id for device_id in catalog_ids
                 if device_id not in by_id]
    check("catalog_devices_covered", not uncovered,
          f"uncovered={uncovered}")
    for device_id in uncovered:
        err(f"设备 {device_id!r} 缺少绑定")

    for device in catalog.devices:
        binding = by_id.get(device.id)
        if binding is None:
            continue
        if binding.kind != device.kind:
            err(f"设备 {device.id!r} 类型 {device.kind!r} 与绑定 "
                f"{binding.kind!r} 不一致")

    orphan = sorted(device_id for device_id in by_id
                    if device_id not in catalog_ids)
    check("bindings_match_catalog", not orphan, f"orphan={orphan}")
    for device_id in orphan:
        err(f"绑定 {device_id!r} 不在设备目录中")

    # 2. 五台机械臂实例齐全 (06-20: 全部实例静态核对).
    arm_ids = sorted(device.id for device in catalog.devices
                     if device.kind == "arm")
    expected_arms = [f"arm_{unit_id}" for unit_id in cfg.UNIT_IDS]
    check("arm_instances_complete", arm_ids == expected_arms,
          f"catalog={arm_ids} expected={expected_arms}")
    if arm_ids != expected_arms:
        err(f"机械臂实例不完整: {arm_ids} != {expected_arms}")

    # 3. 资源/区域必须来自权威表.
    for row in (*bindings, *supports):
        for resource in row.resources:
            if resource not in domain_rules.RESOURCE_ORDER:
                err(f"{_row_id(row)}: 未知资源 {resource!r}")
        for zone in row.zones:
            if zone not in domain_rules.ZONE_CAPACITY:
                err(f"{_row_id(row)}: 未知区域 {zone!r}")

    # 4. 别名: 扁平下划线、唯一、可在场景配置中解析.
    known = _known_aliases()
    owner: dict[str, str] = {}
    for row in (*bindings, *supports):
        row_id = _row_id(row)
        for alias in row.aliases:
            if not alias or "/" in alias:
                err(f"{row_id}: 非法别名 {alias!r} (必须扁平下划线)")
                continue
            if alias in owner and owner[alias] != row_id:
                err(f"别名 {alias!r} 同时属于 {owner[alias]!r} 与 {row_id!r}")
            owner[alias] = row_id
            if alias not in known:
                err(f"{row_id}: 别名 {alias!r} 不在场景配置中")
    check("aliases_unique", len(owner) == sum(
        len(row.aliases) for row in (*bindings, *supports)),
        f"aliases={len(owner)}")

    # 5. 运行时组件键必须与设备类型匹配.
    for binding in bindings:
        expected = _DEVICE_RUNTIME_KEYS.get(binding.kind)
        if expected is None:
            err(f"{binding.device_id}: 未知设备类型 {binding.kind!r}")
        elif binding.runtime_keys != expected:
            err(f"{binding.device_id}: 运行时键 {binding.runtime_keys} "
                f"!= 期望 {expected}")

    # 6. 工序闭合: 默认目录的每个工序都能被其候选设备的绑定承接.
    for recipe in catalog.recipes:
        for operation in recipe.operations:
            for device_id in operation.candidate_devices:
                binding = by_id.get(device_id)
                if binding is None:
                    continue
                missing_resources = [resource for resource
                                     in operation.resources
                                     if resource not in binding.resources]
                if missing_resources:
                    err(f"{device_id}: 工序 {operation.id} 资源未绑定 "
                        f"{missing_resources}")
                missing_zones = sorted(_port_zones(operation)
                                       - set(binding.zones))
                if missing_zones:
                    err(f"{device_id}: 工序 {operation.id} 区域未绑定 "
                        f"{missing_zones}")
                if operation.dock:
                    dock_resource = f"dock_{operation.dock}"
                    if dock_resource not in binding.resources:
                        err(f"{device_id}: 工序 {operation.id} 需要停靠位 "
                            f"{dock_resource!r}, 未登记在绑定资源中")
                if operation.executor_kind == "arm":
                    conveyor_zones = []
                    if operation.source_kind == "conveyor":
                        conveyor_zones.extend(
                            port.zone for port in operation.inputs)
                    if operation.sink_kind == "conveyor" and \
                            operation.output is not None:
                        conveyor_zones.append(operation.output.zone)
                    for zone in conveyor_zones:
                        if zone not in cfg.LINE_IDS:
                            err(f"{device_id}: 工序 {operation.id} 的传送带"
                                f"区域 {zone!r} 不是物流线")
                        elif zone != binding.default_line_id:
                            err(f"{device_id}: 工序 {operation.id} 使用 "
                                f"{zone!r}, 与默认线 "
                                f"{binding.default_line_id!r} 不一致")
                if operation.executor_kind == "agv":
                    for dock in (operation.route_origin_dock,
                                 operation.route_destination_dock):
                        dock_resource = f"dock_{dock}"
                        if dock_resource not in binding.resources:
                            err(f"{device_id}: 工序 {operation.id} 路线端 "
                                f"{dock_resource!r} 未登记在绑定资源中")

    # 7. 资源/区域闭合: 资源表与区域表都应有物理承载.
    claimed_resources: list[str] = []
    claimed_zones: list[str] = []
    for row in (*bindings, *supports):
        for resource in row.resources:
            if resource not in claimed_resources:
                claimed_resources.append(resource)
        for zone in row.zones:
            if zone not in claimed_zones:
                claimed_zones.append(zone)
    unclaimed_resources = [resource
                           for resource in domain_rules.RESOURCE_ORDER
                           if resource not in claimed_resources]
    unclaimed_zones = [zone for zone in domain_rules.ZONE_CAPACITY
                       if zone not in claimed_zones]
    check("resources_claimed", not unclaimed_resources,
          f"unclaimed={unclaimed_resources}")
    for resource in unclaimed_resources:
        err(f"资源 {resource!r} 没有任何设备或承载绑定")
    check("zones_claimed", not unclaimed_zones,
          f"unclaimed={unclaimed_zones}")
    for zone in unclaimed_zones:
        err(f"区域 {zone!r} 没有任何设备或承载绑定")

    # 8. 合流工序必须由逻辑设备承接.
    for recipe in catalog.recipes:
        if not recipe.join_operation_id:
            continue
        join = catalog.operation(recipe.join_operation_id, recipe.id)
        if not join.candidate_devices:
            err(f"合流工序 {join.id} 缺少候选设备")
            continue
        binding = by_id.get(join.candidate_devices[0])
        if binding is None or binding.kind != "logic":
            err(f"合流工序 {join.id} 未绑定到逻辑设备")
            continue
        if binding.aliases:
            err(f"{binding.device_id}: 逻辑设备不应有场景对象别名")
        missing_zones = sorted(_port_zones(join) - set(binding.zones))
        if missing_zones:
            err(f"{binding.device_id}: 合流区域未绑定 {missing_zones}")

    return {
        "ok": not errors,
        "errors": errors,
        "checks": checks,
        "devices": len(bindings),
        "supports": len(supports),
        "aliases": len(owner),
        "resources": len(claimed_resources),
        "zones": len(claimed_zones),
    }


# ═══════════════════════════════════════════════════════════════
# 场景核对 (需要已加载场景)
# ═══════════════════════════════════════════════════════════════

def verify_scene_bindings(
        sim: Any, scene: Any,
        catalog: Optional[ProcessCatalog] = None) -> dict:
    """在已加载场景上核对别名可解析、互不冲突、工位句柄一致.

    ``scene`` 需提供 ``handle(alias) -> int`` (缺失返回 -1); 若还有 ``units``
    映射, 则交叉核对五个工位的缓存句柄。本函数只读, 不创建/删除对象。
    """
    errors: list[str] = []
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    handles: dict[str, int] = {}
    missing: list[str] = []
    for row in (*device_bindings(), *support_bindings()):
        row_id = _row_id(row)
        for alias in row.aliases:
            try:
                handle = int(scene.handle(alias))
            except Exception as exc:          # 场景未加载或句柄失效
                errors.append(f"{row_id}: 解析别名 {alias!r} 失败: {exc}")
                continue
            if handle < 0:
                missing.append(f"{row_id}:{alias}")
            else:
                handles[alias] = handle
    check("aliases_resolved", not missing, f"missing={missing[:8]}")
    for row in missing:
        errors.append(f"场景缺少对象: {row}")

    collisions: list[str] = []
    owner: dict[int, str] = {}
    for alias, handle in handles.items():
        if handle in owner:
            collisions.append(f"{owner[handle]} / {alias}")
        else:
            owner[handle] = alias
    check("aliases_distinct", not collisions, f"collisions={collisions[:8]}")
    for row in collisions:
        errors.append(f"别名指向同一场景对象: {row}")

    units = getattr(scene, "units", None) or {}
    mismatched: list[str] = []
    for unit_id in cfg.UNIT_IDS:
        unit = units.get(unit_id)
        if unit is None:
            continue
        aliases_cfg = cfg.UNIT_ALIASES[unit_id]
        for role, attr in _UNIT_HANDLE_ATTRS.items():
            cached = int(getattr(unit, attr, -1))
            resolved = handles.get(aliases_cfg[role], -1)
            if cached != resolved:
                mismatched.append(
                    f"{unit_id}.{role}: cached={cached} alias={resolved}")
    check("unit_handles_match", not mismatched,
          f"mismatched={mismatched[:8]}")
    for row in mismatched:
        errors.append(f"工位缓存句柄与别名不一致: {row}")

    report: dict = {
        "ok": not errors,
        "errors": errors,
        "checks": checks,
        "aliases": len(handles),
        "missing": len(missing),
        "units_checked": sorted(units),
        "sim_time": None,
    }
    if sim is not None:
        try:
            report["sim_time"] = round(float(sim.getSimulationTime()), 4)
        except Exception:
            pass
    if catalog is not None:
        static = validate_bindings(catalog)
        report["static_ok"] = static["ok"]
        report["static_errors"] = static["errors"]
        report["ok"] = report["ok"] and static["ok"]
    return report
