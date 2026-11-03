"""阶段 2 步骤 1: 设备绑定表与默认工艺目录的一致性 (不需要 CoppeliaSim)."""

from __future__ import annotations

from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.process_model import DeviceSpec, ProcessCatalog
from flexible_factory.domain.rules import RESOURCE_ORDER, ZONE_CAPACITY
from flexible_factory.simulation import config as cfg
from flexible_factory.simulation.device_binding import (
    DeviceBindingError, binding_for, device_bindings, support_bindings,
    validate_bindings, verify_scene_bindings)


class _FakeScene:
    """只实现别名解析的最小场景替身."""

    def __init__(self, missing=(), shared=None):
        self._missing = set(missing)
        self._shared = dict(shared or {})
        self._handles: dict[str, int] = {}
        self.units: dict = {}

    def handle(self, alias: str) -> int:
        if alias in self._missing:
            return -1
        if alias in self._shared:
            return int(self._shared[alias])
        if alias not in self._handles:
            self._handles[alias] = 100 + len(self._handles)
        return self._handles[alias]


def _catalog_with_extra_device(device: DeviceSpec) -> ProcessCatalog:
    base_catalog = default_catalog()
    return ProcessCatalog(
        recipes=base_catalog.recipes,
        capabilities=base_catalog.capabilities,
        devices=(*base_catalog.devices, device),
        zones=base_catalog.zones,
        known_resources=base_catalog.known_resources,
        version=base_catalog.version)


# ═══════════════════════════════════════════════════════════════
# 目录覆盖与臂实例
# ═══════════════════════════════════════════════════════════════

def test_default_catalog_bindings_validate_cleanly():
    report = validate_bindings(default_catalog())
    assert report["ok"], report["errors"]
    assert report["devices"] == 7           # 五臂 + AGV + 逻辑处理器
    assert report["supports"] == 8          # 四条线 + 交接台 + 两缓冲 + 输出
    assert all(row["ok"] for row in report["checks"])


def test_every_catalog_device_has_one_binding_of_same_kind():
    catalog = default_catalog()
    bindings = {row.device_id: row for row in device_bindings()}
    assert sorted(bindings) == sorted(device.id for device in catalog.devices)
    for device in catalog.devices:
        assert bindings[device.id].kind == device.kind


def test_five_arms_are_bound_with_catalog_default_lines():
    catalog = default_catalog()
    arm_ids = sorted(device.id for device in catalog.devices
                     if device.kind == "arm")
    assert arm_ids == ["arm_A", "arm_B", "arm_C", "arm_D", "arm_E"]
    for device in catalog.devices:
        binding = binding_for(device.id)
        if device.kind != "arm":
            continue
        assert binding.default_line_id == device.default_line_id
        assert device.default_line_id in binding.resources
        unit_id = device.id.removeprefix("arm_")
        unit_aliases = cfg.UNIT_ALIASES[unit_id]
        for role in ("robot", "suction", "tcp", "suction_sensor",
                     "model_sensor", "ik_target", "island", "station"):
            assert unit_aliases[role] in binding.aliases


def test_agv_binding_covers_lanes_docks_and_dock_sensors():
    binding = binding_for("agv_1")
    for lane in cfg.AGV_LANES:
        assert lane in binding.resources
    for dock in cfg.AGV_DOCK_IDS:
        assert f"dock_{dock}" in binding.resources
        assert cfg.ALIASES["dock_sensor"][dock] in binding.aliases
    assert binding.zones == ("agv",)


def test_logic_binding_has_no_scene_objects():
    binding = binding_for("logic_E")
    assert binding.kind == "logic"
    assert binding.aliases == ()
    assert binding.resources == ()
    assert set(binding.zones) == {"buffer_e_c", "buffer_e_d", "output_e"}


def test_binding_for_unknown_device_raises():
    try:
        binding_for("arm_Z")
    except DeviceBindingError as exc:
        assert exc.code == "V1_DEVICE_BINDING"
        assert "arm_Z" in str(exc)
    else:
        raise AssertionError("未知设备必须抛 DeviceBindingError")


# ═══════════════════════════════════════════════════════════════
# 资源 / 区域 / 别名闭合
# ═══════════════════════════════════════════════════════════════

def test_binding_resources_and_zones_come_from_authoritative_tables():
    for row in (*device_bindings(), *support_bindings()):
        for resource in row.resources:
            assert resource in RESOURCE_ORDER, (row, resource)
        for zone in row.zones:
            assert zone in ZONE_CAPACITY, (row, zone)


def test_every_resource_and_zone_is_claimed_by_a_binding():
    claimed_resources = {resource for row in (*device_bindings(),
                                              *support_bindings())
                         for resource in row.resources}
    claimed_zones = {zone for row in (*device_bindings(), *support_bindings())
                     for zone in row.zones}
    assert list(RESOURCE_ORDER) == [resource for resource in RESOURCE_ORDER
                                    if resource in claimed_resources]
    assert list(ZONE_CAPACITY) == [zone for zone in ZONE_CAPACITY
                                   if zone in claimed_zones]


def test_binding_aliases_are_flat_and_owned_once():
    owners: dict[str, str] = {}
    for row in (*device_bindings(), *support_bindings()):
        row_id = getattr(row, "device_id", None) or row.id
        for alias in row.aliases:
            assert alias and "/" not in alias
            assert alias not in owners, f"{alias} 属于 {owners.get(alias)}"
            owners[alias] = row_id


def test_operations_are_covered_by_their_device_bindings():
    catalog = default_catalog()
    for recipe in catalog.recipes:
        for operation in recipe.operations:
            for device_id in operation.candidate_devices:
                binding = binding_for(device_id)
                for resource in operation.resources:
                    assert resource in binding.resources, \
                        (operation.id, device_id, resource)
                if operation.dock:
                    assert f"dock_{operation.dock}" in binding.resources
                zones = {port.zone for port in operation.inputs}
                if operation.output is not None:
                    zones.add(operation.output.zone)
                assert zones <= set(binding.zones), (operation.id, device_id)


def test_reports_extra_catalog_device_as_missing_binding():
    catalog = _catalog_with_extra_device(DeviceSpec(
        id="arm_Z", kind="arm", capabilities=("robotic_pick_place",),
        resources=("arm_Z",), default_line_id="line_s1a"))
    report = validate_bindings(catalog)
    assert not report["ok"]
    assert any("arm_Z" in row for row in report["errors"])
    names = {row["name"] for row in report["checks"]
             if not row["ok"]}
    assert "catalog_devices_covered" in names
    assert "arm_instances_complete" in names


# ═══════════════════════════════════════════════════════════════
# 场景核对 (只测判定逻辑, 不连接 CoppeliaSim)
# ═══════════════════════════════════════════════════════════════

def test_verify_scene_bindings_accepts_complete_scene():
    report = verify_scene_bindings(None, _FakeScene())
    assert report["ok"], report["errors"]
    assert report["missing"] == 0
    assert report["aliases"] > 0


def test_verify_scene_bindings_reports_missing_alias():
    alias = binding_for("arm_A").aliases[0]
    report = verify_scene_bindings(None, _FakeScene(missing=[alias]))
    assert not report["ok"]
    assert report["missing"] == 1
    assert any(alias in row for row in report["errors"])


def test_verify_scene_bindings_reports_alias_collision():
    first = binding_for("arm_A").aliases[0]
    second = binding_for("agv_1").aliases[0]
    report = verify_scene_bindings(
        None, _FakeScene(shared={first: 7, second: 7}))
    assert not report["ok"]
    assert any("同一场景对象" in row for row in report["errors"])


def test_verify_scene_bindings_includes_static_report():
    report = verify_scene_bindings(None, _FakeScene(),
                                   catalog=default_catalog())
    assert report["static_ok"] is True
    assert report["ok"] is True
