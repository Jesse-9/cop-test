"""assembly_mvp: Neo4j 图谱混线装配 MVP 的纯逻辑/记录型验收测试.

覆盖方案 WP0–WP3 的纯逻辑门槛:
  - 两个产品 fixture 的校验/编译/端口绑定;
  - 记录型 coordinator 的压缩机 → 法兰 → 压缩机混线闭环;
  - 错误端口、跨产品配对、重复消费和父 UID 谱系;
  - 场景扩展的 alias/坐标/静态属性/与 v1 几何不重叠.
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path

import pytest

from flexible_factory.api.server import OrderApiServer
from flexible_factory.application.orders import OrderService
from flexible_factory.domain import assembly_mvp as mvp
from flexible_factory.domain import rules as cfg
from flexible_factory.domain.default_process import default_catalog
from flexible_factory.domain.materials import (ItemType, MaterialRegistry,
                                               RecordState)
from flexible_factory.domain.order_compiler import OrderDagCompiler
from flexible_factory.domain.order_validation import OrderValidator
from flexible_factory.domain.orders import OrderSpec
from flexible_factory.domain.process_model import DIRECT_INPUT_JOIN_HANDLERS
from flexible_factory.persistence.db import (CatalogDatabase,
                                              initialize_database)
from flexible_factory.simulation import assembly_mvp_scene as mvp_scene
from flexible_factory.simulation import contract
from flexible_factory.simulation.layout import default_layout
from test_v1_order_failure_isolation import _coordinator, _run

FIXTURES = (Path(__file__).resolve().parent.parent
            / "docs" / "implementation" / "reference" / "production-flow"
            / "assembly-mvp")
CATALOG = default_catalog()
COMPILER = OrderDagCompiler(CATALOG)
VALIDATOR = OrderValidator(CATALOG)


def _payload(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(
        encoding="utf-8"))


def _order(name: str) -> OrderSpec:
    return OrderSpec.from_dict(_payload(name))


# ═══════════════════════════════════════════════════════════════
# 目录 / 类型 / 配方
# ═══════════════════════════════════════════════════════════════

def test_mvp_catalog_is_finite_and_direct_input():
    catalog = default_catalog()
    assert catalog.validate()["ok"]
    for recipe_id in (mvp.RECIPE_COMPRESSOR, mvp.RECIPE_FLANGE):
        recipe = catalog.recipe(recipe_id)
        assert recipe.direct_input
        assert len(recipe.operations) == 1
        assert not recipe.route_variants and not recipe.entries
        operation = recipe.operations[0]
        assert operation.executor_kind == "logic"
        assert operation.join_handler_id == mvp.JOIN_HANDLER
        assert operation.join_handler_id in DIRECT_INPUT_JOIN_HANDLERS
        assert [port.port_id for port in operation.inputs] == [
            port_id for _zone, _item, port_id in
            mvp.PRODUCTS[recipe_id]["inputs"]]
        assert operation.output.item_type.value == \
            mvp.PRODUCTS[recipe_id]["product_type"]


def test_mvp_item_types_and_zones_are_bounded_extensions():
    # v1 冻结类型元组不变 (受保护工件 material_model.types 契约).
    assert cfg.ITEM_TYPES == (
        "RAW_A", "RAW_B", "SEMI_AB", "SEMI_AC", "SEMI_CE", "RAW_D",
        "PART_DE", "FINAL")
    for item_type in mvp.ITEM_TYPES:
        assert item_type in {row.value for row in ItemType}
        assert item_type in cfg.ITEM_COLORS
    assert set(mvp.ZONES) <= set(cfg.ZONE_CAPACITY)
    registry = MaterialRegistry()
    for zone in mvp.SOURCE_ZONES:
        assert registry.capacity_of(zone) == 1
        assert registry.zone_free(zone)


def test_contract_profile_declares_mvp_types_without_v1_change():
    full = contract.build_contract_extra(profile="full_flow")
    assert full["scene_profile"] == "full_flow"
    assert full["material_model"]["types"] == list(cfg.ITEM_TYPES)
    assert full["material_model"]["extension_types"] == []
    assert full["assembly_mvp"] is None
    mvp_extra = contract.build_contract_extra(profile="assembly_mvp")
    assert mvp_extra["scene_profile"] == "assembly_mvp"
    assert mvp_extra["material_model"]["types"] == [
        *cfg.ITEM_TYPES, *mvp.ITEM_TYPES]
    assert mvp_extra["material_model"]["extension_types"] == \
        list(mvp.ITEM_TYPES)
    assert mvp_extra["assembly_mvp"]["profile"] == "assembly_mvp"
    # profile 不改变 v1 config/layout 摘要.
    assert mvp_extra["config"]["digest"] == full["config"]["digest"]
    assert mvp_extra["layout"]["digest"] == full["layout"]["digest"]


# ═══════════════════════════════════════════════════════════════
# 订单契约 / 编译
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,product,plan_types", [
    ("order_compressor", mvp.ITEM_TYPE_COMPRESSOR_ASSY,
     (mvp.ITEM_TYPE_BASEPLATE, mvp.ITEM_TYPE_COMPRESSOR)),
    ("order_flange", mvp.ITEM_TYPE_FLANGE_ASSY,
     (mvp.ITEM_TYPE_FRONT_FLANGE, mvp.ITEM_TYPE_REAR_FLANGE)),
])
def test_fixture_validates_and_compiles(name, product, plan_types):
    order = _order(name)
    preview = COMPILER.preview(order, run_id="preview")
    assert preview.ok, preview.diagnostics
    compiled = COMPILER.compile(order, run_id="run_mvp")
    assert len(compiled.instances) == 1
    instance = compiled.instances[0]
    assert instance.template_id == \
        mvp.PRODUCTS[
            mvp.RECIPE_COMPRESSOR if name.endswith("compressor")
            else mvp.RECIPE_FLANGE]["template_id"]
    assert instance.uid == 3
    assert instance.predecessor_instance_ids == []
    assert [plan.uid for plan in compiled.material_plans] == [1, 2, 3]
    assert [plan.item_type for plan in compiled.material_plans] == \
        [*plan_types, product]
    assert compiled.context.target_item_type == product
    assert compiled.context.target_instance_ids == (instance.instance_id,)


def test_wrong_port_is_rejected():
    payload = _payload("order_compressor")
    payload["inputs"][0]["port_id"] = "baseplate-in"
    order = OrderSpec.from_dict(payload)
    codes = {item.code for item in VALIDATOR.validate(order)}
    assert "ORDER_INPUT_TYPE_MISMATCH" in codes
    with pytest.raises(Exception):
        COMPILER.compile(order, run_id="bad")


def test_cross_product_binding_is_rejected():
    payload = _payload("order_compressor")
    payload["nodes"][0]["template_id"] = mvp.TEMPLATE_FLANGE
    payload["inputs"][0]["port_id"] = "front-in"
    payload["inputs"][1]["port_id"] = "rear-in"
    order = OrderSpec.from_dict(payload)
    codes = {item.code for item in VALIDATOR.validate(order)}
    assert {"ORDER_INPUT_TYPE_MISMATCH", "ORDER_INPUT_ZONE_MISMATCH"} & codes
    assert not COMPILER.preview(order).ok


def test_missing_component_is_not_a_complete_order():
    payload = _payload("order_compressor")
    payload["inputs"] = payload["inputs"][:1]
    order = OrderSpec.from_dict(payload)
    codes = [item.code for item in VALIDATOR.validate(order)]
    assert "ORDER_PORT_UNSUPPLIED" in codes
    assert not COMPILER.preview(order).ok


def test_direct_input_is_not_allowed_on_v1_join():
    payload = _payload("order_compressor")
    payload["nodes"][0]["template_id"] = "kit_join"
    payload["inputs"][0].update({
        "zone": "buffer_e_c", "item_type": "SEMI_CE",
        "port_id": "housing-input"})
    payload["inputs"][1].update({
        "zone": "buffer_e_d", "item_type": "PART_DE",
        "port_id": "bracket-input"})
    order = OrderSpec.from_dict(payload)
    codes = {item.code for item in VALIDATOR.validate(order)}
    assert "ORDER_JOIN_PORT_INPUT" in codes


def test_second_join_in_one_order_is_out_of_scope():
    payload = _payload("order_compressor")
    payload["nodes"].append(
        {"node_id": "assemble2", "template_id": mvp.TEMPLATE_FLANGE})
    order = OrderSpec.from_dict(payload)
    codes = {item.code for item in VALIDATOR.validate(order)}
    assert {"ORDER_TARGET_UNREACHABLE", "ORDER_NODE_DANGLING",
            "ORDER_NODE_UNREACHABLE"} & codes


# ═══════════════════════════════════════════════════════════════
# 记录型 coordinator: 单产品与混线序列
# ═══════════════════════════════════════════════════════════════

def _run_order(name: str, run_id: str) -> tuple[OrderSpec, object]:
    order = _order(name)
    compiled = COMPILER.compile(order, run_id=run_id)
    coordinator = _coordinator(order_plan=compiled)
    coordinator.start(0.0)
    _run(coordinator)
    return order, coordinator


def test_compressor_order_closes_ledger_and_genealogy():
    order, coordinator = _run_order("order_compressor", "run_compressor")
    assert coordinator.order_status() == "SUCCEEDED"
    assert coordinator.acceptance()["ok"] is True
    assert coordinator.final_count == 1
    final = coordinator.flow.final_records()[0]
    assert final.item_type is ItemType.COMPRESSOR_ASSY
    assert final.parents == (2, 1)       # 端口顺序: compressor, baseplate
    assert final.zone == "mvp_output_compressor_assy"
    assert [row["uid"] for row in coordinator.spawned] == [1, 2]
    assert coordinator.flow.join_history[0]["pair_key"] == "2+1"
    assert coordinator.flow.join_history[0]["output_type"] == \
        "COMPRESSOR_ASSY"
    genealogy = coordinator.flow.genealogy(final.uid)
    assert [row["uid"] for row in genealogy["parents"]] == [2, 1]
    join_event = next(row for row in coordinator.events
                      if row["kind"] == "FINAL_CREATED")
    assert join_event["detail"]["order_id"] == order.order_id
    assert join_event["detail"]["handler_id"] == mvp.JOIN_HANDLER
    assert [row["port_id"] for row in
            join_event["detail"]["input_ports"]] == [
                "compressor-in", "baseplate-in"]
    assert join_event["detail"]["input_ports"][0]["uid"] == 2


def test_mixed_line_sequence_compressor_flange_compressor():
    """同一场景可按固定序列切换两个产品; 每笔独立编译/运行/账本."""
    expected = (
        ("order_compressor", ItemType.COMPRESSOR_ASSY,
         "mvp_output_compressor_assy"),
        ("order_flange", ItemType.FLANGE_ASSY, "mvp_output_flange_assy"),
        ("order_compressor", ItemType.COMPRESSOR_ASSY,
         "mvp_output_compressor_assy"),
    )
    for index, (name, product, zone) in enumerate(expected):
        order, coordinator = _run_order(name, f"run_mixed_{index}")
        assert coordinator.order_status() == "SUCCEEDED"
        finals = coordinator.flow.final_records()
        assert len(finals) == 1
        assert finals[0].item_type is product
        assert finals[0].zone == zone
        assert len(finals[0].parents) == 2
        assert coordinator.flow.wip()["total"] == 0
        assert all(coordinator.flow.by_uid(uid).state is RecordState.CONSUMED
                   for uid in finals[0].parents)
        # 每笔订单只消费自己的两个输入 UID.
        assert set(finals[0].parents) == {1, 2}
        assert not coordinator.failures


def test_missing_one_input_never_produces_product():
    registry = MaterialRegistry()
    registry.register(1, ItemType.COMPRESSOR, "Part_0001",
                      zone=mvp.ZONE_SOURCE_COMPRESSOR)
    result = registry.commit_pair(
        output_uid=3, parent_uids=(1, 2), sim_time=1.0,
        output_alias="Part_0003",
        input_ports=((mvp.ZONE_SOURCE_COMPRESSOR, ItemType.COMPRESSOR),
                     (mvp.ZONE_SOURCE_BASEPLATE, ItemType.BASEPLATE)),
        output_type=ItemType.COMPRESSOR_ASSY,
        output_zone=mvp.ZONE_OUTPUT_COMPRESSOR)
    assert not result.ok and "MISSING_INPUT" in result.reason
    assert not registry.final_records()
    assert registry.by_uid(1).state is not RecordState.CONSUMED


def test_duplicate_consumption_and_wrong_pair_are_rejected():
    registry = MaterialRegistry()
    registry.register(1, ItemType.COMPRESSOR, "Part_0001",
                      zone=mvp.ZONE_SOURCE_COMPRESSOR)
    registry.register(2, ItemType.BASEPLATE, "Part_0002",
                      zone=mvp.ZONE_SOURCE_BASEPLATE)
    ports = ((mvp.ZONE_SOURCE_COMPRESSOR, ItemType.COMPRESSOR),
             (mvp.ZONE_SOURCE_BASEPLATE, ItemType.BASEPLATE))
    first = registry.commit_pair(
        output_uid=3, parent_uids=(1, 2), sim_time=1.0,
        output_alias="Part_0003", input_ports=ports,
        output_type=ItemType.COMPRESSOR_ASSY,
        output_zone=mvp.ZONE_OUTPUT_COMPRESSOR)
    assert first.ok
    replay = registry.commit_pair(
        output_uid=4, parent_uids=(1, 2), sim_time=2.0,
        output_alias="Part_0004", input_ports=ports,
        output_type=ItemType.COMPRESSOR_ASSY,
        output_zone=mvp.ZONE_OUTPUT_COMPRESSOR)
    assert not replay.ok
    assert replay.reason in ("ALREADY_CONSUMED", "DUPLICATE_PAIR")
    assert len(registry.final_records()) == 1

    registry.register(5, ItemType.FRONT_FLANGE, "Part_0005",
                      zone=mvp.ZONE_SOURCE_FRONT_FLANGE)
    registry.register(6, ItemType.REAR_FLANGE, "Part_0006",
                      zone=mvp.ZONE_SOURCE_REAR_FLANGE)
    wrong = registry.commit_pair(
        output_uid=7, parent_uids=(5, 6), sim_time=3.0,
        output_alias="Part_0007", input_ports=ports,
        output_type=ItemType.COMPRESSOR_ASSY,
        output_zone=mvp.ZONE_OUTPUT_COMPRESSOR)
    assert not wrong.ok and "TYPE_MISMATCH" in wrong.reason
    assert registry.by_uid(5).state is not RecordState.CONSUMED
    assert not registry.by_uid(7)


def test_compressor_and_flange_never_cross_consume():
    registry = MaterialRegistry()
    for uid, item_type, zone in (
            (1, ItemType.COMPRESSOR, mvp.ZONE_SOURCE_COMPRESSOR),
            (2, ItemType.BASEPLATE, mvp.ZONE_SOURCE_BASEPLATE),
            (3, ItemType.FRONT_FLANGE, mvp.ZONE_SOURCE_FRONT_FLANGE),
            (4, ItemType.REAR_FLANGE, mvp.ZONE_SOURCE_REAR_FLANGE)):
        registry.register(uid, item_type, f"Part_{uid:04d}", zone=zone)
    result = registry.commit_pair(
        output_uid=5, parent_uids=(1, 3), sim_time=1.0,
        output_alias="Part_0005",
        input_ports=((mvp.ZONE_SOURCE_COMPRESSOR, ItemType.COMPRESSOR),
                     (mvp.ZONE_SOURCE_BASEPLATE, ItemType.BASEPLATE)),
        output_type=ItemType.COMPRESSOR_ASSY,
        output_zone=mvp.ZONE_OUTPUT_COMPRESSOR)
    assert not result.ok
    assert result.reason.startswith("TYPE_MISMATCH")
    assert all(rec.state is RecordState.CREATED
               for rec in registry.all())


# ═══════════════════════════════════════════════════════════════
# 场景扩展 (假 sim; 不连接 CoppeliaSim)
# ═══════════════════════════════════════════════════════════════

class _FakeSim:
    handle_world = 0
    primitiveshape_cuboid = 1
    object_shape_type = 1
    object_dummy_type = 2
    object_joint_type = 3
    object_proximitysensor_type = 4
    object_visionsensor_type = 5
    object_forcesensor_type = 6
    object_graph_type = 7
    object_light_type = 8
    object_camera_type = 9
    object_mirror_type = 10
    object_octree_type = 11
    object_pointcloud_type = 12
    object_script_type = 13
    shapeintparam_static = 100
    shapeintparam_kinematic = 101
    shapeintparam_respondable = 102
    shapeintparam_respondable_mask = 103

    def __init__(self) -> None:
        self.objects: dict[int, int] = {}
        self.alias: dict[int, str] = {}
        self.pos: dict[int, list[float]] = {}
        self.parent: dict[int, int] = {}
        self.params: dict[int, dict[int, int]] = {}
        self.size: dict[int, list[float]] = {}
        self._next = 0

    def _add(self, object_type: int, size=None) -> int:
        self._next += 1
        handle = self._next
        self.objects[handle] = object_type
        self.pos[handle] = [0.0, 0.0, 0.0]
        self.parent[handle] = -1
        self.params[handle] = {}
        if size is not None:
            self.size[handle] = list(size)
        return handle

    def createPrimitiveShape(self, shape_type, size):
        return self._add(self.object_shape_type, size)

    def createDummy(self, size):
        return self._add(self.object_dummy_type)

    def setObjectAlias(self, handle, alias):
        self.alias[handle] = alias

    def setObjectParent(self, handle, parent, keep):
        self.parent[handle] = int(parent)

    def setObjectPosition(self, handle, position, frame):
        self.pos[handle] = [float(value) for value in position]

    def setObjectPose(self, handle, pose, frame):
        self.pos[handle] = [float(value) for value in pose[:3]]

    def setObjectColor(self, *args, **kwargs):
        pass

    def setObjectInt32Param(self, handle, param, value):
        self.params[handle][int(param)] = int(value)

    def getObjectInt32Param(self, handle, param):
        return self.params[handle].get(int(param), 0)

    def getObjects(self, index, object_type):
        rows = [handle for handle in sorted(self.objects)
                if self.objects[handle] == object_type]
        return rows[index] if index < len(rows) else -1

    def getObjectAlias(self, handle):
        return self.alias.get(handle, "")

    def getObjectType(self, handle):
        return self.objects[handle]

    def getObjectParent(self, handle):
        return self.parent[handle]

    def getObjectPosition(self, handle, frame):
        return list(self.pos[handle])

    def getShapeBB(self, handle):
        return list(self.size[handle])

    def removeObjects(self, handles):
        for handle in handles:
            self.objects.pop(handle, None)


class _FakeScene:
    def __init__(self, sim: _FakeSim) -> None:
        self.sim = sim
        self.handles = {"Island": self._root()}
        self.stage4_layout = default_layout()
        self.scene_profile = "full_flow"

    def _root(self) -> int:
        handle = self.sim._add(self.sim.object_dummy_type)
        self.sim.alias[handle] = "Island"
        return handle

    def handle(self, key):
        return self.handles.get(key, -1)


def test_scene_extension_builds_and_verifies():
    sim = _FakeSim()
    scene = _FakeScene(sim)
    info = mvp_scene.build_extension(sim, scene)
    assert set(info["handles"]) == set(mvp_scene.ALIASES)
    assert scene.scene_profile == mvp.PROFILE
    checks = mvp_scene.verify_extension(sim, scene)
    failed = [row for row in checks if not row["ok"]]
    assert not failed, failed[:5]
    for zone in mvp.SOURCE_ZONES:
        source = mvp_scene.order_sources()[zone]
        assert source["position"] == mvp_scene.material_pose(zone)
    assert mvp_scene.material_pose(mvp.ZONE_COMPRESSOR_ASSEMBLY) is None
    # 扩展对象全部静态且不可碰撞.
    for alias in mvp_scene.ALIASES:
        handle = scene.handles[alias]
        if sim.objects[handle] != sim.object_shape_type:
            continue
        assert sim.getObjectInt32Param(
            handle, sim.shapeintparam_static) == 1
        assert sim.getObjectInt32Param(
            handle, sim.shapeintparam_respondable) == 0


def test_scene_extension_verify_detects_position_drift():
    sim = _FakeSim()
    scene = _FakeScene(sim)
    mvp_scene.build_extension(sim, scene)
    handle = scene.handles["MVP_Source_COMPRESSOR"]
    sim.pos[handle] = [0.0, 0.0, 0.0]
    failed = {row["name"] for row in
              mvp_scene.verify_extension(sim, scene) if not row["ok"]}
    assert "mvp_zone_position:mvp_source_compressor" in failed


def test_extension_contract_payload_roundtrip():
    payload = json.loads(json.dumps(mvp_scene.contract_payload()))
    assert mvp_scene.extension_contract_mismatches(payload) == []
    tampered = deepcopy(payload)
    tampered["extension_version"] = "tampered"
    assert mvp_scene.extension_contract_mismatches(tampered)


# ═══════════════════════════════════════════════════════════════
# 运行记录: 成品/谱系写入 Q1–Q4
# ═══════════════════════════════════════════════════════════════

def test_mvp_run_record_persists_product_genealogy(tmp_path):
    from flexible_factory.application.report import (report_genealogy,
                                                     report_run)
    from flexible_factory.persistence.runs import RunRecorder

    database = CatalogDatabase(tmp_path / "mvp_records.db")
    initialize_database(database)
    order, coordinator = _run_order("order_compressor", "run_records")
    recorder = RunRecorder(database, run_id="mvp_record", scenario="order",
                           artifact_id="mvp-artifact")
    assert recorder.start(sim_time=0.0)["ok"]
    result = recorder.finish(coordinator, coordinator.summary())
    assert result["ok"] is True and result["status"] == "DONE"
    assert result["counts"]["genealogy_edges"] == 2
    report = report_run(database, "mvp_record")
    assert report["final_uids"] == [3]
    assert report["counts"]["material_types"] == {
        "BASEPLATE": 1, "COMPRESSOR": 1, "COMPRESSOR_ASSY": 1}
    tree = report_genealogy(database, uid=3, run_id="mvp_record")["tree"]
    assert sorted(row["material"]["uid"] for row in tree["parents"]) == [1, 2]
    assert tree["material"]["item_type"] == "COMPRESSOR_ASSY"
    assert order.order_id == "mvp-compressor-001"


# ═══════════════════════════════════════════════════════════════
# 现有订单服务/HTTP API 路径
# ═══════════════════════════════════════════════════════════════

def test_fixture_through_order_service_and_http_api(tmp_path):
    from test_v1_order_service import (_request, _success_summary,
                                       _wait_status)

    database = CatalogDatabase(tmp_path / "mvp_orders.db")
    initialize_database(database)
    service = OrderService(
        database, artifact_id="mvp-artifact",
        runner=lambda command, stop: _success_summary(command))
    server = OrderApiServer(service, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = _payload("order_compressor")
        status, body = _request(server.base_url, "GET", "/api/templates")
        assert status == 200
        template_ids = {row["template_id"] for row in body["templates"]}
        assert {mvp.TEMPLATE_COMPRESSOR, mvp.TEMPLATE_FLANGE} <= template_ids
        templates = {row["template_id"]: row for row in body["templates"]}
        assert templates[mvp.TEMPLATE_COMPRESSOR]["join_handler_id"] == \
            mvp.JOIN_HANDLER

        status, body = _request(server.base_url, "POST",
                                "/api/orders/preview", payload)
        assert status == 200 and body["preview"]["ok"] is True

        status, body = _request(server.base_url, "POST", "/api/orders",
                                payload)
        assert status == 200, body
        assert body["order"]["order_id"] == "mvp-compressor-001"

        status, body = _request(
            server.base_url, "POST",
            "/api/orders/mvp-compressor-001/versions/1/start",
            {"artifact_id": "mvp-artifact"})
        assert status == 200, body
        run = _wait_status(server.base_url, body["run"]["run_id"])
        assert run["status"] == "SUCCEEDED"
    finally:
        server.shutdown()