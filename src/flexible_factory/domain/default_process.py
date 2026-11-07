"""default_process.py — v1 UFLOW 当前唯一默认工艺配方 (06-19 §1A).

本模块把原先散落在 ``application/coordinator.py`` 的 ``UNIT_TASKS`` / ``TASK_RESOURCES``
/ ``TASK_TYPES`` / ``TASK_SOURCE/SINK/DOCK`` / 区域映射 / ``FOLLOWUP`` 合并
为一份可校验的 :class:`~stage4.process_model.RecipeSpec`.

阶段 0-1 仍是代码内默认流程, 不加载 YAML/JSON 用户配方. 配置文件
``config.py`` 中的旧 ``TASK_KIND_*``、``TASK_FLOW``、``TASK_PRIORITY``
仍保留, 因为它们参与场景工件 config digest 和旧报告 schema; 运行时不再
读取这些流程映射. :func:`validate_default_against_config` 在启动时核对
两者一致, 防止目录与冻结工件契约漂移.

业务语义仅用于解释已有物料流: 壳体/支架的"加工"是当前抓取、转运和物料
类型转换的抽象; E 岛是逻辑配对, 不是真实物理装配.
"""

from __future__ import annotations

from functools import lru_cache

from .. import spec
from . import rules as cfg
from .materials import ItemType
from .process_model import (CapabilitySpec, DeviceSpec, MaterialPort,
                            OperationTemplate, OrderEntry, ProcessCatalog,
                            ProcessModelError, RecipeSpec, RouteVariant,
                            WorkStepTemplateRef, ZoneSpec)

# 修改 recipe/operation/route/设备能力时递增此版本; 它不参与场景工件
# config digest, 由报告与未来 manifest 记录.
# 20261106a: 增加订单 DAG 编排元数据 (端口 identity、编排 allowlist、
# 场景范围、join handler、可生成来料区域).
PROCESS_MODEL_VERSION = "v1_process_20261106a"
DEFAULT_RECIPE_ID = "mixed_line_v1"
DEFAULT_PRODUCT_ID = "assembled_module"

_ROBOTIC_PICK_PLACE = "robotic_pick_place"
_AGV_TRANSPORT = "agv_transport"
_LOGICAL_JOIN = "logical_join"


def _step(template_id: str) -> WorkStepTemplateRef:
    return WorkStepTemplateRef(template_id)


def _arm_operation(*, operation_id: str, kind: str, semantics: str,
                   device: str, inputs: tuple[MaterialPort, ...],
                   output: MaterialPort, source_kind: str,
                   sink_kind: str, resources: tuple[str, ...],
                   priority: int, successors: tuple[str, ...] = (),
                   depends_on: tuple[str, ...] = (),
                   predecessor_policy: str = "all",
                   dock: str = "",
                   work_steps: tuple[WorkStepTemplateRef, ...] = ()) -> \
        OperationTemplate:
    return OperationTemplate(
        id=operation_id, kind=kind, semantics=semantics,
        executor_kind="arm", capability=_ROBOTIC_PICK_PLACE,
        candidate_devices=(device,), inputs=inputs, output=output,
        source_kind=source_kind, sink_kind=sink_kind,
        resources=resources, resource_policy="fixed",
        work_steps=work_steps or (_step("conveyor_pickup"),
                                  _step("arm_pick_place")),
        completion_verifier="unit_handoff_verified",
        priority=priority, depends_on=depends_on,
        predecessor_policy=predecessor_policy,
        successors=successors, dock=dock,
        orchestratable=True, scene_id=spec.LAYOUT_NAME)


def _transport_operation(*, operation_id: str, kind: str, semantics: str,
                         item_type: ItemType, origin: str,
                         destination: str, depends_on: tuple[str, ...],
                         successors: tuple[str, ...],
                         priority: int) -> OperationTemplate:
    port = MaterialPort("agv", item_type)
    return OperationTemplate(
        id=operation_id, kind=kind, semantics=semantics,
        executor_kind="agv", capability=_AGV_TRANSPORT,
        candidate_devices=("agv_1",),
        inputs=(port,), output=port,
        source_kind="agv", sink_kind="agv",
        resources=(), resource_policy="agv_route",
        work_steps=(_step("agv_route"),),
        completion_verifier="agv_route_verified",
        priority=priority, depends_on=depends_on, successors=successors,
        dock=destination, route_origin_dock=origin,
        route_destination_dock=destination,
        orchestratable=True, scene_id=spec.LAYOUT_NAME)


def _build_operations() -> tuple[OperationTemplate, ...]:
    housing_direct_a = _arm_operation(
        operation_id="housing_direct_A", kind=cfg.TASK_KIND_A_CONVEYOR,
        semantics="S1→A: conveyor raw → A → handoff_ac",
        device="arm_A",
        inputs=(MaterialPort("line_s1a", ItemType.RAW_A),),
        output=MaterialPort("handoff_ac", ItemType.SEMI_AC),
        source_kind="conveyor", sink_kind="station",
        resources=("line_s1a", "handoff_ac", "zone_ab", "zone_ac", "arm_A"),
        priority=1, successors=("housing_finish",), dock="A")

    housing_alt_b = _arm_operation(
        operation_id="housing_alt_B", kind=cfg.TASK_KIND_B,
        semantics="S1→B: conveyor raw → B → AGV@dock_B",
        device="arm_B",
        inputs=(MaterialPort("line_s1b", ItemType.RAW_B),),
        output=MaterialPort("agv", ItemType.SEMI_AB),
        source_kind="conveyor", sink_kind="agv",
        resources=("line_s1b", "dock_B", "zone_ab", "arm_B"),
        priority=1, successors=("transport_BA",), dock="B")

    transport_ba = _transport_operation(
        operation_id="transport_BA", kind=cfg.TASK_KIND_TRANSPORT_BA,
        semantics="B→A: AGV loaded dock_B → dock_A → A → handoff_ac",
        item_type=ItemType.SEMI_AB, origin="B", destination="A",
        depends_on=("housing_alt_B",), successors=("housing_alt_A",),
        priority=0)

    housing_alt_a = _arm_operation(
        operation_id="housing_alt_A", kind=cfg.TASK_KIND_A_AGV,
        semantics="AGV@dock_A → A → handoff_ac (绕行壳体汇入 A)",
        device="arm_A",
        inputs=(MaterialPort("agv", ItemType.SEMI_AB),),
        output=MaterialPort("handoff_ac", ItemType.SEMI_AC),
        source_kind="agv", sink_kind="station",
        resources=("handoff_ac", "dock_A", "zone_ab", "zone_ac", "arm_A"),
        priority=0, depends_on=("transport_BA",),
        successors=("housing_finish",), dock="A",
        work_steps=(_step("arm_pick_place"),))

    housing_finish = _arm_operation(
        operation_id="housing_finish", kind=cfg.TASK_KIND_C,
        semantics="A→C: handoff_ac → C → line_ce",
        device="arm_C",
        inputs=(MaterialPort("handoff_ac", ItemType.SEMI_AC),),
        output=MaterialPort("line_ce", ItemType.SEMI_CE),
        source_kind="station", sink_kind="conveyor",
        resources=("line_ce", "handoff_ac", "zone_ac", "arm_C"),
        priority=1,
        depends_on=("housing_direct_A", "housing_alt_A"),
        predecessor_policy="any", successors=("housing_to_E_buffer",),
        work_steps=(_step("arm_pick_place"),))

    housing_to_e = _arm_operation(
        operation_id="housing_to_E_buffer", kind=cfg.TASK_KIND_E_CONVEYOR,
        semantics="C→E: line_ce → E → buffer_e_c",
        device="arm_E",
        inputs=(MaterialPort("line_ce", ItemType.SEMI_CE),),
        output=MaterialPort("buffer_e_c", ItemType.SEMI_CE),
        source_kind="conveyor", sink_kind="station",
        resources=("line_ce", "buffer_e_c", "arm_E"),
        priority=1, depends_on=("housing_finish",),
        successors=("kit_join",), dock="E")

    bracket_process = _arm_operation(
        operation_id="bracket_process", kind=cfg.TASK_KIND_D,
        semantics="S2→D: conveyor raw → D → AGV@dock_D",
        device="arm_D",
        inputs=(MaterialPort("line_s2d", ItemType.RAW_D),),
        output=MaterialPort("agv", ItemType.PART_DE),
        source_kind="conveyor", sink_kind="agv",
        resources=("line_s2d", "dock_D", "arm_D"),
        priority=1, successors=("transport_DE",), dock="D")

    transport_de = _transport_operation(
        operation_id="transport_DE", kind=cfg.TASK_KIND_TRANSPORT_DE,
        semantics="D→E: AGV loaded dock_D → dock_E → E → buffer_e_d",
        item_type=ItemType.PART_DE, origin="D", destination="E",
        depends_on=("bracket_process",), successors=("bracket_to_E",),
        priority=1)

    bracket_to_e = _arm_operation(
        operation_id="bracket_to_E", kind=cfg.TASK_KIND_E_AGV,
        semantics="AGV@dock_E → E → buffer_e_d (支架独立分支)",
        device="arm_E",
        inputs=(MaterialPort("agv", ItemType.PART_DE),),
        output=MaterialPort("buffer_e_d", ItemType.PART_DE),
        source_kind="agv", sink_kind="station",
        resources=("buffer_e_d", "dock_E", "arm_E"),
        priority=0, depends_on=("transport_DE",),
        successors=("kit_join",), dock="E",
        work_steps=(_step("arm_pick_place"),))

    kit_join = OperationTemplate(
        id="kit_join", kind="logical_join",
        semantics="E join: buffer_e_c + buffer_e_d → FINAL@output_e",
        executor_kind="logic", capability=_LOGICAL_JOIN,
        candidate_devices=("logic_E",),
        inputs=(MaterialPort("buffer_e_c", ItemType.SEMI_CE,
                             port_id="housing-input"),
                MaterialPort("buffer_e_d", ItemType.PART_DE,
                             port_id="bracket-input")),
        output=MaterialPort("output_e", ItemType.FINAL),
        source_kind="none", sink_kind="none",
        resources=(), resource_policy="logical_join",
        work_steps=(_step("logical_join"),),
        completion_verifier="join_pair_committed",
        priority=0,
        depends_on=("housing_to_E_buffer", "bracket_to_E"),
        predecessor_policy="all",
        orchestratable=True, scene_id=spec.LAYOUT_NAME,
        output_port_id="output-0",
        join_handler_id="e_material_pair")

    return (housing_direct_a, housing_alt_b, transport_ba, housing_alt_a,
            housing_finish, housing_to_e, bracket_process, transport_de,
            bracket_to_e, kit_join)


def _build_route_variants() -> tuple[RouteVariant, ...]:
    housing_direct = RouteVariant(
        id="housing_direct", group="housing_supply",
        semantics="壳体直达 A→C 路线",
        operation_ids=("housing_direct_A", "housing_finish",
                       "housing_to_E_buffer"),
        entry_zone="line_s1a", exit_zone="buffer_e_c")
    housing_alt = RouteVariant(
        id="housing_alt", group="housing_supply",
        semantics="壳体 B→AGV→A 绕行路线; 与 housing_direct 互斥",
        operation_ids=("housing_alt_B", "transport_BA", "housing_alt_A",
                       "housing_finish", "housing_to_E_buffer"),
        entry_zone="line_s1b", exit_zone="buffer_e_c")
    bracket = RouteVariant(
        id="bracket_route", group="bracket_supply",
        semantics="支架 S2→D→AGV→E 独立供料路线",
        operation_ids=("bracket_process", "transport_DE", "bracket_to_E"),
        entry_zone="line_s2d", exit_zone="buffer_e_d")
    return (housing_direct, housing_alt, bracket)


def _build_entries() -> tuple[OrderEntry, ...]:
    """有限批次的入口 → 路线固定对应.

    同一 ``route_group`` 内的路线互斥; ``RAW_A`` 走直达, ``RAW_B`` 走绕行,
    ``RAW_D`` 走支架路线. 这是按入口条目的静态选择, 不是动态选路.
    """
    return (
        OrderEntry(id="s1a", operation_id="housing_direct_A",
                   line="line_s1a", item_type=ItemType.RAW_A,
                   route_group="housing_supply",
                   route_variant_id="housing_direct"),
        OrderEntry(id="s1b", operation_id="housing_alt_B",
                   line="line_s1b", item_type=ItemType.RAW_B,
                   route_group="housing_supply",
                   route_variant_id="housing_alt"),
        OrderEntry(id="s2d", operation_id="bracket_process",
                   line="line_s2d", item_type=ItemType.RAW_D,
                   route_group="bracket_supply",
                   route_variant_id="bracket_route"),
    )


def _build_devices() -> tuple[DeviceSpec, ...]:
    # 默认线按当前单元控制器实际使用的几何绑定, 不是工序语义:
    # A/B 用各自输入带, C/E 用 C→E 带, D 用 S2→D 带.
    default_lines = {
        "A": "line_s1a", "B": "line_s1b", "C": "line_ce",
        "D": "line_s2d", "E": "line_ce",
    }
    devices = [
        DeviceSpec(id=f"arm_{unit_id}", kind="arm",
                   capabilities=(_ROBOTIC_PICK_PLACE,),
                   resources=(f"arm_{unit_id}",),
                   default_line_id=default_lines[unit_id])
        for unit_id in cfg.UNIT_IDS]
    devices.append(DeviceSpec(id="agv_1", kind="agv",
                              capabilities=(_AGV_TRANSPORT,)))
    devices.append(DeviceSpec(id="logic_E", kind="logic",
                              capabilities=(_LOGICAL_JOIN,),
                              physical=False))
    return tuple(devices)


def _build_zones() -> tuple[ZoneSpec, ...]:
    descriptions = {
        "line_s1a": "S1→A 输入带/取料区",
        "line_s1b": "S1→B 输入带/取料区",
        "line_s2d": "S2→D 输入带/取料区",
        "line_ce": "C→E 传送带 (C 放置 + E 取料)",
        "handoff_ac": "A→C 固定交接台",
        "buffer_e_c": "E 岛壳体输入缓冲",
        "buffer_e_d": "E 岛支架输入缓冲",
        "agv": "AGV 载台",
        "output_e": "E 岛成品输出",
    }
    spawnable = {"line_s1a", "line_s1b", "line_s2d"}
    return tuple(ZoneSpec(id=zone_id, capacity=int(capacity),
                          description=descriptions.get(zone_id, ""),
                          spawnable=zone_id in spawnable)
                 for zone_id, capacity in cfg.ZONE_CAPACITY.items())


def build_default_catalog() -> ProcessCatalog:
    """构造并校验默认目录; 每次调用返回新对象, 供测试注入变体."""
    recipe = RecipeSpec(
        id=DEFAULT_RECIPE_ID, version=PROCESS_MODEL_VERSION,
        product_type=DEFAULT_PRODUCT_ID,
        operations=_build_operations(),
        route_variants=_build_route_variants(),
        entries=_build_entries(),
        join_operation_id="kit_join")
    catalog = ProcessCatalog(
        recipes=(recipe,),
        capabilities=(
            CapabilitySpec(_ROBOTIC_PICK_PLACE, "机械臂取放/转运"),
            CapabilitySpec(_AGV_TRANSPORT, "AGV 按路线运输"),
            CapabilitySpec(_LOGICAL_JOIN, "无运动逻辑配对工序"),
        ),
        devices=_build_devices(),
        zones=_build_zones(),
        known_resources=cfg.RESOURCE_ORDER,
        version=PROCESS_MODEL_VERSION)
    catalog.validate()
    validate_default_against_config(catalog)
    return catalog


@lru_cache(maxsize=1)
def default_catalog() -> ProcessCatalog:
    """进程内共享的已校验默认目录; coordinator 的默认输入."""
    return build_default_catalog()


def validate_default_against_config(
        catalog: ProcessCatalog,
        recipe_id: str = DEFAULT_RECIPE_ID) -> dict:
    """核对默认目录与 ``config.py`` 中冻结的兼容常量.

    config 值同时参与场景工件 config digest, 阶段 0-1 不修改它们;
    本函数保证运行时目录与冻结工件契约没有漂移. 失败时抛出
    :class:`ProcessModelError`, 信息带 recipe/operation 定位.
    """
    recipe = catalog.recipe(recipe_id)
    errors: list[str] = []

    for kind, priority in cfg.TASK_PRIORITY.items():
        if kind == cfg.TASK_KIND_REPOSITION:
            continue
        try:
            operation = catalog.operation_for_kind(kind)
        except ProcessModelError as exc:
            errors.append(f"recipe={recipe_id}: {exc}")
            continue
        if int(operation.priority) != int(priority):
            errors.append(
                f"recipe={recipe_id} operation={operation.id}: priority "
                f"{operation.priority} != 冻结任务优先级 {priority}")

    derived_transitions: dict[tuple[str, str], str] = {}
    for operation in recipe.operations:
        if operation.executor_kind != "arm" or operation.output is None:
            continue
        device = catalog.device(operation.candidate_devices[0])
        unit_id = device.id.removeprefix("arm_")
        for port in operation.inputs:
            if port.item_type != operation.output.item_type:
                derived_transitions[(unit_id, port.item_type.value)] = \
                    operation.output.item_type.value
    if derived_transitions != dict(cfg.PROCESS_TRANSITIONS):
        errors.append(
            f"recipe={recipe_id}: 工序类型转换与 PROCESS_TRANSITIONS 不一致; "
            f"derived={derived_transitions} "
            f"config={dict(cfg.PROCESS_TRANSITIONS)}")

    if not recipe.join_operation_id:
        errors.append(f"recipe={recipe_id}: 缺少 join_operation_id")
    else:
        join = catalog.operation(recipe.join_operation_id, recipe.id)
        inputs = tuple(port.item_type.value for port in join.inputs)
        if inputs != tuple(cfg.JOIN_INPUTS):
            errors.append(
                f"recipe={recipe_id} operation={join.id}: join 输入 {inputs} "
                f"!= JOIN_INPUTS {tuple(cfg.JOIN_INPUTS)}")
        if join.output is None or join.output.item_type.value != cfg.JOIN_OUTPUT:
            errors.append(
                f"recipe={recipe_id} operation={join.id}: join 输出 "
                f"{None if join.output is None else join.output.item_type.value}"
                f" != JOIN_OUTPUT {cfg.JOIN_OUTPUT}")
        derived_buffers = {port.item_type.value: port.zone
                           for port in join.inputs}
        if derived_buffers != dict(cfg.JOIN_BUFFER_OF):
            errors.append(
                f"recipe={recipe_id} operation={join.id}: join 缓冲 "
                f"{derived_buffers} != JOIN_BUFFER_OF "
                f"{dict(cfg.JOIN_BUFFER_OF)}")

    for entry in recipe.entries:
        source = cfg.SOURCE_SPECS.get(entry.id)
        if source is None:
            errors.append(f"recipe={recipe_id}: 订单入口 {entry.id!r} "
                          f"未在 SOURCE_SPECS 中定义")
            continue
        if source["line"] != entry.line:
            errors.append(
                f"recipe={recipe_id}: 入口 {entry.id!r} line "
                f"{entry.line!r} != SOURCE_SPECS {source['line']!r}")
        if source["item_type"] != entry.item_type.value:
            errors.append(
                f"recipe={recipe_id}: 入口 {entry.id!r} 来料 "
                f"{entry.item_type.value} != SOURCE_SPECS "
                f"{source['item_type']}")

    if errors:
        raise ProcessModelError(
            "默认工艺与冻结配置不兼容:\n" + "\n".join(errors))
    return {"ok": True, "recipe": recipe_id,
            "operations": len(recipe.operations),
            "routes": len(recipe.route_variants),
            "entries": len(recipe.entries)}
