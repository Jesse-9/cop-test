"""contract.py — v1 场景契约: extra.v1 内容与只读校验.

`extra.v1` 记录稳定规格, 不记录运行时句柄:
  - 产品/契约/控制器/协调器/运行入口版本;
  - v1 配置摘要与布局摘要;
  - 布局、五岛拓扑、四条物流线、A→C 固定交接、E 配对规则;
  - 物料类型/谱系/区域容量/背压;
  - 资源表、AGV 路线图、任务流、优先级与验收场景;
  - 来源工件 provenance (只追溯, 运行期不比较 stage2/3 当前摘要).

只读校验不创建影子臂/集合、不修改场景; 任一稳定字段不匹配、发现
`Shadow_*` 残留都判失败.
"""

from __future__ import annotations

from .. import spec
from ..domain import rules as domain
from ..domain.default_process import (DEFAULT_RECIPE_ID,
                                      PROCESS_MODEL_VERSION,
                                      default_catalog)
from ..util import check as _check
from ..util import checks_ok, jsonable, jsonable as _jsonable
from ..util import pick_upper, stable_digest
from . import config as cfg
from . import config_base as base
from . import config_dual as s3cfg
from . import config_motion as s2cfg
from .layout import (Stage4Layout, default_layout, layout_from_dict,
                     validate_layout)

_CONFIG_EXCLUDE = {"MODEL_BASE", "DEFAULT_LOG_FILE"}


def config_summary() -> dict:
    """v1 全部稳定配置的分模块快照."""
    return {
        "base": pick_upper(base, exclude=_CONFIG_EXCLUDE),
        "motion": pick_upper(s2cfg, exclude=_CONFIG_EXCLUDE),
        "dual": pick_upper(s3cfg, exclude=_CONFIG_EXCLUDE),
        "v1": pick_upper(cfg, exclude=_CONFIG_EXCLUDE),
    }


def config_digest() -> str:
    return stable_digest(config_summary())


def layout_digest(layout: Stage4Layout | None = None) -> str:
    layout = default_layout() if layout is None else layout
    return stable_digest(layout.digest_payload())


def build_contract_extra(*, sim=None, scene=None, provenance=None,
                         notes: str = "") -> dict:
    """生成可写入 manifest ``extra.v1`` 的稳定契约."""
    from .agv import route_map

    provenance = dict(provenance or {})
    layout = (scene.stage4_layout if scene is not None
              and getattr(scene, "stage4_layout", None) is not None
              else default_layout())
    units = {
        unit.unit_id: {
            "index": unit.index,
            "arm_base": list(unit.arm_base),
            "island_pos": list(unit.island_pos),
            "station_pos": list(unit.station_pos),
            "dock": (None if unit.dock is None else list(unit.dock)),
            "has_station": unit.has_station,
            "has_dock": unit.has_dock,
            "aliases": unit.aliases,
            "dock_sensor_alias": unit.dock_sensor_alias,
            "targets": {name: list(pose)
                        for name, pose in unit.targets(layout.lines).items()},
        }
        for unit in layout.units
    }
    topology = {
        "islands": list(cfg.UNIT_IDS),
        "sources": ["S1", "S2"],
        "edges": [
            {"id": "S1_to_A", "from": "S1", "to": "A", "medium": "conveyor",
             "line": "line_s1a"},
            {"id": "S1_to_B", "from": "S1", "to": "B", "medium": "conveyor",
             "line": "line_s1b"},
            {"id": "B_to_A", "from": "B", "to": "A", "medium": "agv",
             "priority": 0, "preemption": False},
            {"id": "A_to_C", "from": "A", "to": "C", "medium": "handoff",
             "handoff": cfg.HANDOFF_AC["id"]},
            {"id": "C_to_E", "from": "C", "to": "E", "medium": "conveyor",
             "line": "line_ce"},
            {"id": "S2_to_D", "from": "S2", "to": "D", "medium": "conveyor",
             "line": "line_s2d"},
            {"id": "D_to_E", "from": "D", "to": "E", "medium": "agv",
             "priority": 1,
             "starvation_upgrade_s": domain.D_E_STARVATION_UPGRADE_S},
        ],
    }
    lines = {line_id: line.as_dict()
             for line_id, line in layout.lines.items()}
    material_model = {
        "version": spec.MATERIAL_MODEL_VERSION,
        "types": list(domain.ITEM_TYPES),
        "process_transitions": {
            f"{island}:{source}": target
            for (island, source), target in domain.PROCESS_TRANSITIONS.items()},
        "join": {
            "inputs": list(domain.JOIN_INPUTS),
            "output": domain.JOIN_OUTPUT,
            "buffers": dict(domain.JOIN_BUFFER_OF),
            "atomic_consume": True,
            "duplicate_policy": domain.JOIN_DUPLICATE_POLICY,
            "missing_input": "wait_no_final",
            "geometric_assembly": False,
        },
        "zone_capacity": dict(domain.ZONE_CAPACITY),
        "source_items": {key: {
            "line": item["line"], "item_type": item["item_type"],
            "position": list(item["position"])}
            for key, item in cfg.SOURCE_ITEMS.items()},
        "backpressure": {
            "reasons": dict(cfg.BACKPRESSURE_REASONS),
            "max_wait_s": cfg.BACKPRESSURE_MAX_WAIT_S,
            "no_silent_drop": True,
            "no_auto_retry": True,
        },
        "finite_batch": {name: dict(batch)
                         for name, batch in cfg.SCENARIO_BATCH.items()},
    }
    resources = {
        "policy_version": spec.RESOURCE_POLICY_VERSION,
        "order": list(domain.RESOURCE_ORDER),
        "roles": {name: domain.RESOURCE_ROLES.get(name, "")
                  for name in domain.RESOURCE_ORDER},
        "all_or_nothing": True,
        "agv_route_requires_all_docks": cfg.AGV_ROUTE_REQUIRES_ALL_DOCKS,
        "adjacency_zones": {name: list(pair)
                            for name, pair in
                            cfg.ARM_ADJACENCY_ZONES.items()},
        "rules": [
            "任务一次性申请完整资源集; 任一忙则不持有部分资源",
            "AGV 航线持有实际经过的直道资源与全部 dock; "
            "任一臂 dock 装卸时车辆不通过",
            "臂间互锁区按基座间距导出; 相邻臂不同时运动",
            "失败/停止释放普通调度锁; 安全锁存不由资源释放清除",
        ],
    }
    route = route_map(layout)
    task_flow = {
        "version": spec.TASK_FLOW_VERSION,
        "flow": list(domain.TASK_FLOW),
        "kinds": list(domain.TASK_KINDS),
        "priority": dict(domain.TASK_PRIORITY),
        "process_model_version": PROCESS_MODEL_VERSION,
        "recipe_id": DEFAULT_RECIPE_ID,
        "process_routes": default_catalog().route_lines(),
        "starvation_upgrade_s": domain.D_E_STARVATION_UPGRADE_S,
        "preemption": False,
        "duplicate_policy": domain.DUPLICATE_REQUEST_POLICY,
        "max_attempts": domain.TASK_MAX_ATTEMPTS,
        "queue_capacity": domain.QUEUE_CAPACITY,
        "task_timeout": domain.TASK_TIMEOUT,
        "failed_to_done": False,
    }
    collision = {
        "inter_arm_policy": "all_other_units_in_workcell",
        "conveyor_policy": "line_pads_filtered_by_arm_envelope",
        "safety_latch": "independent_per_unit",
        "no_ompl": True,
        "note": "每台臂仍由 v1 ConfigurationChecker 逐段检查; "
                "其余 4 台真实臂以及本岛线与包络附近物流线的 pad "
                "已加入本臂工作区集合",
    }
    acceptance = {
        "scenarios": list(cfg.STAGE4_SCENARIOS),
        "full_flow": {
            "final_count": 1,
            "unpaired_semi_ce": 1,
            "both_agv_routes": True,
            "priority_decision_logged": True,
        },
        "no_pair": {"final_count": 1},
        "agv_priority": {"priority_decision_ok": True,
                         "no_preemption": True},
        "agv_blocked": {"enter_blocked": True, "resume_after_clear": True,
                        "never_done_while_blocked": True},
        "conveyor_fault": {"affected_task_blocked": True,
                           "no_final": True, "site_preserved": True},
        "exit_criteria": [
            "两类输入到 E 的物流与配对",
            "B→A 与 D→E 实际轮速路线",
            "E 缺件不生成 FINAL, 配对仅生成一个且保留父 UID",
            "有限批次结束并给出完成数/等待/堵塞/异常摘要",
            "一次代表性故障停住受影响支路并保留现场",
        ],
    }
    return {
        "contract_version": spec.SCENE_CONTRACT_VERSION,
        "product_version": spec.PRODUCT_VERSION,
        "controller": {
            "id": spec.CONTROLLER_ID,
            "coordinator": spec.COORDINATOR_ID,
            "runner": spec.RUNNER_ID,
            "phase": "v1",
        },
        "config": {
            "digest": config_digest(),
            "summary": config_summary(),
        },
        "layout": {
            "digest": layout_digest(layout),
            "spec": layout.as_dict(),
            "units": units,
            "lines": lines,
            "height_datum": dict(layout.height_datum),
            "agv_network": {
                "nodes": {key: list(value)
                          for key, value in layout.agv_nodes.items()},
                "edges": [edge.as_dict(layout.agv_nodes)
                          for edge in layout.agv_edges],
                "lanes": list(cfg.AGV_LANES),
                "turn_nodes": list(layout.agv_turn_nodes),
            },
        },
        "topology": topology,
        "material_model": material_model,
        "resources": resources,
        "agv_route": route,
        "task_flow": task_flow,
        "collision": collision,
        "acceptance": acceptance,
        "provenance": provenance,
        "inheritance": provenance,
        "notes": notes,
    }


def _resolve_aliases(sim) -> dict[str, list[int]]:
    from .scene_base import alias_of, all_objects

    index: dict[str, list[int]] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if alias:
            index.setdefault(alias, []).append(int(handle))
    return index


def validate_contract(sim, artifact, scene=None) -> dict:
    manifest = artifact.manifest
    extra = (manifest.get("extra") or {}).get("v1")
    checks: list[dict] = []
    differences: list[dict] = []

    _check(checks, "v1_extra_present", isinstance(extra, dict),
           f"type={type(extra).__name__}")
    if not isinstance(extra, dict):
        return {"ok": False, "checks": checks, "differences": differences}

    _check(checks, "v1_source_phase",
           manifest.get("source_phase") == "v1",
           f"source_phase={manifest.get('source_phase')!r}")
    _check(checks, "v1_contract_version",
           extra.get("contract_version") == spec.SCENE_CONTRACT_VERSION,
           f"saved={extra.get('contract_version')!r} "
           f"current={spec.SCENE_CONTRACT_VERSION!r}")
    controller = extra.get("controller") or {}
    for key, expected in (("id", spec.CONTROLLER_ID),
                          ("coordinator", spec.COORDINATOR_ID),
                          ("runner", spec.RUNNER_ID)):
        _check(checks, f"v1_{key}", controller.get(key) == expected,
               f"saved={controller.get(key)!r} current={expected!r}")

    saved_digest = (extra.get("config") or {}).get("digest")
    current_digest = config_digest()
    _check(checks, "v1_config_digest", saved_digest == current_digest,
           f"saved={saved_digest} current={current_digest}")
    if saved_digest != current_digest:
        differences.append({"kind": "v1_config", "saved": saved_digest,
                            "current": current_digest})

    layout_saved = (extra.get("layout") or {}).get("digest")
    layout_current = layout_digest()
    _check(checks, "v1_layout_digest", layout_saved == layout_current,
           f"saved={layout_saved} current={layout_current}")
    if layout_saved != layout_current:
        differences.append({"kind": "v1_layout", "saved": layout_saved,
                            "current": layout_current})

    provenance = extra.get("provenance") or {}
    _check(checks, "v1_provenance_present",
           bool(provenance.get("source_artifact_id")),
           f"source={provenance.get('source_artifact_id')}")

    resources = extra.get("resources") or {}
    _check(checks, "v1_resource_order",
           tuple(resources.get("order") or ()) == tuple(domain.RESOURCE_ORDER),
           f"saved={resources.get('order')}")
    topology = extra.get("topology") or {}
    _check(checks, "v1_topology_islands",
           list(topology.get("islands") or ()) == list(cfg.UNIT_IDS),
           f"saved={topology.get('islands')}")
    edge_ids = {edge.get("id") for edge in topology.get("edges") or ()}
    expected_edges = {"S1_to_A", "S1_to_B", "B_to_A", "A_to_C",
                      "C_to_E", "S2_to_D", "D_to_E"}
    _check(checks, "v1_topology_edges", edge_ids == expected_edges,
           f"saved={sorted(edge_ids)}")
    route = extra.get("agv_route") or {}
    expected_docks = {
        unit_id: [round(float(v), 6) for v in pos]
        for unit_id, pos in default_layout().dock_positions.items()}
    saved_docks = {str(key): [round(float(v), 6) for v in value]
                   for key, value in (route.get("docks") or {}).items()}
    _check(checks, "v1_route_docks", saved_docks == expected_docks,
           f"saved={saved_docks}")
    _check(checks, "v1_route_lanes",
           list(route.get("lanes") or ()) == list(cfg.AGV_LANES),
           f"saved={route.get('lanes')}")
    _check(checks, "v1_route_turn_nodes",
           list(route.get("turn_nodes") or ())
           == list(default_layout().agv_turn_nodes),
           f"saved={route.get('turn_nodes')}")
    layout_extra = extra.get("layout") or {}
    saved_datum = layout_extra.get("height_datum") or {}
    _check(checks, "v1_height_datum",
           abs(float(saved_datum.get("Z_base", -1.0)) - cfg.Z_BASE) <= 1e-9
           and abs(float(saved_datum.get("H_transfer", -1.0))
                   - cfg.H_TRANSFER) <= 1e-9,
           f"saved_Z_base={saved_datum.get('Z_base')} "
           f"saved_H_transfer={saved_datum.get('H_transfer')}")
    saved_network = layout_extra.get("agv_network") or {}
    _check(checks, "v1_agv_network_nodes",
           {key: [round(float(v), 6) for v in value]
            for key, value in (saved_network.get("nodes") or {}).items()}
           == {key: [round(float(v), 6) for v in value]
               for key, value in default_layout().agv_nodes.items()},
           f"saved={saved_network.get('nodes')}")
    material_model = extra.get("material_model") or {}
    _check(checks, "v1_material_types",
           list(material_model.get("types") or ()) == list(domain.ITEM_TYPES),
           f"saved={material_model.get('types')}")
    join = material_model.get("join") or {}
    _check(checks, "v1_join_inputs",
           list(join.get("inputs") or ()) == list(domain.JOIN_INPUTS),
           f"saved={join.get('inputs')}")
    _check(checks, "v1_join_output",
           join.get("output") == domain.JOIN_OUTPUT,
           f"saved={join.get('output')}")
    flow = extra.get("task_flow") or {}
    _check(checks, "v1_task_kinds",
           set(flow.get("kinds") or ()) == set(domain.TASK_KINDS),
           f"saved={flow.get('kinds')}")
    saved_process_version = flow.get("process_model_version")
    if saved_process_version is not None:
        _check(checks, "v1_process_model_version",
               saved_process_version == PROCESS_MODEL_VERSION,
               f"saved={saved_process_version!r} "
               f"current={PROCESS_MODEL_VERSION!r}")

    index = _resolve_aliases(sim)
    for alias in cfg.REQUIRED_ALIASES:
        hits = index.get(alias, [])
        _check(checks, f"v1_alias_unique:{alias}", len(hits) == 1,
               f"{len(hits)} 个匹配")
    shadow = sorted({alias for alias, hits in index.items()
                     if alias.startswith(cfg.SESSION_RESOURCE_PREFIX)
                     for _ in hits})
    _check(checks, "v1_no_session_resources", not shadow,
           f"found={shadow}")

    if scene is not None:
        try:
            units = getattr(scene, "units", {})
            _check(checks, "v1_scene_units",
                   set(units) == set(cfg.UNIT_IDS), f"units={sorted(units)}")
            for unit_id in cfg.UNIT_IDS:
                unit = units.get(unit_id)
                if unit is None:
                    _check(checks, f"v1_unit:{unit_id}", False, "missing")
                    continue
                _check(checks, f"v1_unit:{unit_id}",
                       len(unit.robot_joints) == 6,
                       f"joints={len(unit.robot_joints)}")
                modes = []
                for joint in unit.robot_joints:
                    mode = sim.getJointMode(joint)
                    modes.append(int(mode[0] if isinstance(mode, (tuple, list))
                                     else mode))
                _check(checks, f"v1_joint_modes:{unit_id}",
                       all(mode == int(sim.jointmode_kinematic)
                           for mode in modes), f"modes={modes}")
                local = list(sim.getObjectPosition(unit.tcp,
                                                   unit.suction_root))
                _check(checks, f"v1_tcp_offset:{unit_id}",
                       all(abs(local[i] - base.TCP_LOCAL_POS[i])
                           <= base.SUCTION_MOUNT_POS_TOL for i in range(3)),
                       f"TCP local={[round(v, 5) for v in local]}")
                if unit.spec.has_dock:
                    pose = list(sim.getObjectPosition(
                        unit.dock_sensor, sim.handle_world))
                    expected = [unit.spec.dock[0], unit.spec.dock[1],
                                base.AGV_DOCK_SENSOR_Z]
                    _check(checks, f"v1_dock_sensor:{unit_id}",
                           all(abs(pose[i] - expected[i]) <= 0.002
                               for i in range(3)),
                           f"pos={[round(v, 4) for v in pose]}")
            report = scene.verify()
            _check(checks, "v1_scene_contract", bool(report.get("ok")),
                   f"{sum(1 for c in report.get('checks', []) if c['ok'])}"
                   f"/{len(report.get('checks', []))} checks")
            for item in report.get("checks", []):
                if not item.get("ok"):
                    differences.append({"kind": "contract",
                                        "name": item.get("name"),
                                        "detail": item.get("detail")})
        except Exception as exc:
            _check(checks, "v1_scene_observable", False,
                   f"{type(exc).__name__}: {exc}")

    ok = checks_ok(checks) and not differences
    return {
        "ok": ok,
        "contract_version": extra.get("contract_version"),
        "checks": checks,
        "differences": differences,
    }


def validate_layout_or_raise(layout: Stage4Layout) -> dict:
    report = validate_layout(layout)
    if not report.get("ok"):
        raise ValueError(f"布局校验失败: {report.get('differences')}")
    return report
