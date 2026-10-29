"""scene_contract.py — 阶段四工件契约: extra.stage4 与只读校验.

`extra.stage4` 记录**稳定规格**, 不记录运行时句柄:
  - 契约/控制器/协调器/运行入口版本;
  - 阶段四配置摘要与布局摘要;
  - 父 stage3 工件、stage3 契约版本与配置摘要;
  - 五岛拓扑、四条物流线、A→C 固定交接、E 配对规则;
  - 物料类型/谱系/区域容量/背压;
  - 资源表、AGV 路线图、任务流、优先级与验收场景.

只读校验不创建影子臂/集合、不修改场景; 任一稳定字段不匹配、发现
`Shadow_*` 残留、父 stage3 契约漂移都判失败.
"""

from __future__ import annotations

from stage1 import config as base
from stage1 import scene_artifact as sa
from stage1.scene import alias_of, all_objects, subtree

from stage2 import config as s2cfg
from stage2.scene_contract import (sanitize_manifest_sections as s2_sanitize,
                                   stage2_config_digest)

from stage3 import config as s3cfg
from stage3.scene_contract import (stage3_config_digest,
                                   sanitize_manifest_sections as s3_sanitize)

from . import config as cfg
from .layout import (Stage4Layout, default_layout, layout_from_dict,
                     validate_layout)


def stage4_config_summary() -> dict:
    out = {}
    exclude = {"DEFAULT_LOG_FILE"}
    for name, value in vars(cfg).items():
        if name.startswith("_") or not name.isupper() or name in exclude:
            continue
        if name == "base" or callable(value):
            continue
        out[name] = sa._jsonable(value)
    return out


def stage4_config_digest() -> str:
    return sa.stable_digest(stage4_config_summary())


def stage4_layout_digest(layout: Stage4Layout | None = None) -> str:
    layout = default_layout() if layout is None else layout
    return sa.stable_digest(layout.digest_payload())


def sanitize_manifest_sections(manifest: dict) -> dict:
    return s3_sanitize(s2_sanitize(manifest))


def diff_stage4_manifest(parent: dict, current: dict) -> list[dict]:
    return sa.diff_manifest(sanitize_manifest_sections(parent),
                            sanitize_manifest_sections(current))


def build_stage4_extra(*, sim=None, scene=None, parent_info=None,
                       notes: str = "") -> dict:
    """生成可写入 manifest ``extra.stage4`` 的稳定契约."""
    from .agv_map import route_map
    parent_info = dict(parent_info or {})
    layout = (scene.stage4_layout if scene is not None
              and getattr(scene, "stage4_layout", None) is not None
              else default_layout())
    units = {
        spec.unit_id: {
            "index": spec.index,
            "arm_base": list(spec.arm_base),
            "island_pos": list(spec.island_pos),
            "station_pos": list(spec.station_pos),
            "dock": (None if spec.dock is None else list(spec.dock)),
            "has_station": spec.has_station,
            "has_dock": spec.has_dock,
            "aliases": spec.aliases,
            "dock_sensor_alias": spec.dock_sensor_alias,
            "targets": {name: list(pose)
                        for name, pose in spec.targets().items()},
        }
        for spec in layout.units
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
             "starvation_upgrade_s": cfg.D_E_STARVATION_UPGRADE_S},
        ],
    }
    lines = {line_id: spec.as_dict()
             for line_id, spec in layout.lines.items()}
    material_model = {
        "version": cfg.STAGE4_MATERIAL_MODEL_VERSION,
        "types": list(cfg.ITEM_TYPES),
        "process_transitions": {
            f"{island}:{source}": target
            for (island, source), target in cfg.PROCESS_TRANSITIONS.items()},
        "join": {
            "inputs": list(cfg.JOIN_INPUTS),
            "output": cfg.JOIN_OUTPUT,
            "buffers": dict(cfg.JOIN_BUFFER_OF),
            "atomic_consume": True,
            "duplicate_policy": cfg.JOIN_DUPLICATE_POLICY,
            "missing_input": "wait_no_final",
            "geometric_assembly": False,
        },
        "zone_capacity": dict(cfg.ZONE_CAPACITY),
        "source_items": {key: {
            "line": spec["line"], "item_type": spec["item_type"],
            "position": list(spec["position"])}
            for key, spec in cfg.SOURCE_ITEMS.items()},
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
        "policy_version": cfg.STAGE4_RESOURCE_POLICY_VERSION,
        "order": list(cfg.RESOURCE_ORDER),
        "roles": {name: cfg.RESOURCE_ROLES.get(name, "")
                  for name in cfg.RESOURCE_ORDER},
        "all_or_nothing": True,
        "agv_route_requires_all_docks": cfg.AGV_ROUTE_REQUIRES_ALL_DOCKS,
        "adjacency_zones": {name: list(pair)
                           for name, pair in
                           cfg.ARM_ADJACENCY_ZONES.items()},
        "rules": [
            "任务一次性申请完整资源集; 任一忙则不持有部分资源",
            "AGV 航线持有 agv_lane 与全部 dock; 任一臂 dock 装卸时车辆不通过",
            "臂间互锁区按基座间距导出; 相邻臂不同时运动",
            "失败/停止释放普通调度锁; 安全锁存不由资源释放清除",
        ],
    }
    route = route_map(layout)
    task_flow = {
        "version": cfg.STAGE4_TASK_FLOW_VERSION,
        "flow": list(cfg.TASK_FLOW),
        "kinds": list(cfg.TASK_KINDS),
        "priority": dict(cfg.TASK_PRIORITY),
        "starvation_upgrade_s": cfg.D_E_STARVATION_UPGRADE_S,
        "preemption": False,
        "duplicate_policy": cfg.DUPLICATE_REQUEST_POLICY,
        "max_attempts": cfg.TASK_MAX_ATTEMPTS,
        "queue_capacity": cfg.QUEUE_CAPACITY,
        "task_timeout": cfg.TASK_TIMEOUT,
        "failed_to_done": False,
    }
    collision = {
        "inter_arm_policy": "all_other_units_in_workcell",
        "conveyor_policy": "line_pads_filtered_by_arm_envelope",
        "safety_latch": "independent_per_unit",
        "no_ompl": True,
        "note": "每台臂仍由阶段二 ConfigurationChecker 逐段检查; "
                "其余 4 台真实臂, 以及本岛线与包络附近物流线的 pad "
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
    inheritance = {
        "parent_artifact": parent_info.get("artifact_id"),
        "parent_manifest_digest": parent_info.get("manifest_digest"),
        "parent_source_phase": parent_info.get("source_phase", "stage3"),
        "parent_stage3_contract_version": parent_info.get(
            "stage3_contract_version"),
        "parent_stage3_config_digest": parent_info.get(
            "stage3_config_digest"),
        "parent_stage3_layout_digest": parent_info.get(
            "stage3_layout_digest"),
        "parent_stage2_contract_version": parent_info.get(
            "stage2_contract_version"),
        "parent_stage2_config_digest": parent_info.get(
            "stage2_config_digest"),
        "scene_changed": True,
        "inherited": [
            "A_unit", "B_unit", "stage3_shared_conveyor", "agv_model",
            "stage1_tool_mount", "stage1_material_physics",
            "stage2_collision_and_safety", "stage3_b_unit_layout",
        ],
        "changed_scene_parameters": [
            "added Island_C/D/E with Robot_UR5_03/04/05 and suction tools",
            "added Conveyor_S1_to_B / Conveyor_S2_to_D / Conveyor_C_to_E",
            "added S2 warehouse, source dummies and dock D/E sensors",
            "added A→C handoff sensor, E buffers and E output",
            "all conveyor __config__.targetVel set to 0 so lines start "
            "stopped until a unit task commands them",
            "A/B coordinates, AGV initial pose and floor unchanged",
        ],
    }
    extra = {
        "contract_version": cfg.STAGE4_CONTRACT_VERSION,
        "controller": {
            "id": cfg.STAGE4_CONTROLLER_ID,
            "coordinator": cfg.STAGE4_COORDINATOR_ID,
            "runner": cfg.STAGE4_RUNNER_ID,
            "phase": cfg.STAGE4_PHASE,
        },
        "config": {
            "digest": stage4_config_digest(),
            "summary": stage4_config_summary(),
        },
        "layout": {
            "digest": stage4_layout_digest(layout),
            "spec": layout.as_dict(),
            "units": units,
            "lines": lines,
        },
        "topology": topology,
        "material_model": material_model,
        "resources": resources,
        "agv_route": route,
        "task_flow": task_flow,
        "collision": collision,
        "acceptance": acceptance,
        "inheritance": inheritance,
        "notes": notes,
    }
    return extra


# ═══════════════════════════════════════════════════════════════
# 只读校验
# ═══════════════════════════════════════════════════════════════

def _check(checks: list[dict], name: str, ok: bool,
           detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def _resolve_aliases(sim) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for handle in all_objects(sim):
        alias = alias_of(sim, handle)
        if alias:
            index.setdefault(alias, []).append(int(handle))
    return index


def validate_stage4_contract(sim, artifact, scene=None) -> dict:
    manifest = artifact.manifest
    extra = (manifest.get("extra") or {}).get("stage4")
    checks: list[dict] = []
    differences: list[dict] = []

    _check(checks, "stage4_extra_present", isinstance(extra, dict),
           f"type={type(extra).__name__}")
    if not isinstance(extra, dict):
        return {"ok": False, "checks": checks, "differences": differences}

    _check(checks, "stage4_source_phase",
           manifest.get("source_phase") == cfg.STAGE4_PHASE,
           f"source_phase={manifest.get('source_phase')!r}")
    _check(checks, "stage4_contract_version",
           extra.get("contract_version") == cfg.STAGE4_CONTRACT_VERSION,
           f"saved={extra.get('contract_version')!r} "
           f"current={cfg.STAGE4_CONTRACT_VERSION!r}")
    controller = extra.get("controller") or {}
    for key, expected in (("id", cfg.STAGE4_CONTROLLER_ID),
                          ("coordinator", cfg.STAGE4_COORDINATOR_ID),
                          ("runner", cfg.STAGE4_RUNNER_ID)):
        _check(checks, f"stage4_{key}", controller.get(key) == expected,
               f"saved={controller.get(key)!r} current={expected!r}")

    saved_digest = (extra.get("config") or {}).get("digest")
    current_digest = stage4_config_digest()
    _check(checks, "stage4_config_digest", saved_digest == current_digest,
           f"saved={saved_digest} current={current_digest}")
    if saved_digest != current_digest:
        differences.append({"kind": "stage4_config", "saved": saved_digest,
                            "current": current_digest})

    layout_saved = (extra.get("layout") or {}).get("digest")
    layout_current = stage4_layout_digest()
    _check(checks, "stage4_layout_digest", layout_saved == layout_current,
           f"saved={layout_saved} current={layout_current}")
    if layout_saved != layout_current:
        differences.append({"kind": "stage4_layout", "saved": layout_saved,
                            "current": layout_current})

    inheritance = extra.get("inheritance") or {}
    parent = manifest.get("parent_artifact") or {}
    _check(checks, "stage4_parent_recorded",
           bool(inheritance.get("parent_artifact")),
           f"parent={inheritance.get('parent_artifact')}")
    if parent.get("artifact_id") and inheritance.get("parent_artifact"):
        _check(checks, "stage4_parent_matches_manifest",
               parent.get("artifact_id") == inheritance.get("parent_artifact"),
               f"manifest={parent.get('artifact_id')}")
    _check(checks, "stage4_parent_source_phase",
           inheritance.get("parent_source_phase") == cfg.PARENT_PHASE,
           f"parent_source_phase="
           f"{inheritance.get('parent_source_phase')!r}")
    _check(checks, "stage4_scene_changed",
           inheritance.get("scene_changed") is True,
           f"scene_changed={inheritance.get('scene_changed')}")
    _check(checks, "stage4_stage3_contract_version",
           inheritance.get("parent_stage3_contract_version")
           == s3cfg.STAGE3_CONTRACT_VERSION,
           f"saved={inheritance.get('parent_stage3_contract_version')!r} "
           f"current={s3cfg.STAGE3_CONTRACT_VERSION!r}")
    _check(checks, "stage4_stage3_config_digest",
           inheritance.get("parent_stage3_config_digest")
           == stage3_config_digest(),
           f"saved={inheritance.get('parent_stage3_config_digest')} "
           f"current={stage3_config_digest()}")
    _check(checks, "stage4_stage2_contract_version",
           inheritance.get("parent_stage2_contract_version")
           == s2cfg.STAGE2_CONTRACT_VERSION,
           f"saved={inheritance.get('parent_stage2_contract_version')!r} "
           f"current={s2cfg.STAGE2_CONTRACT_VERSION!r}")
    _check(checks, "stage4_stage2_config_digest",
           inheritance.get("parent_stage2_config_digest")
           == stage2_config_digest(),
           f"saved={inheritance.get('parent_stage2_config_digest')} "
           f"current={stage2_config_digest()}")

    resources = extra.get("resources") or {}
    _check(checks, "stage4_resource_order",
           tuple(resources.get("order") or ()) == tuple(cfg.RESOURCE_ORDER),
           f"saved={resources.get('order')}")
    topology = extra.get("topology") or {}
    _check(checks, "stage4_topology_islands",
           list(topology.get("islands") or ()) == list(cfg.UNIT_IDS),
           f"saved={topology.get('islands')}")
    edge_ids = {edge.get("id") for edge in topology.get("edges") or ()}
    expected_edges = {"S1_to_A", "S1_to_B", "B_to_A", "A_to_C",
                      "C_to_E", "S2_to_D", "D_to_E"}
    _check(checks, "stage4_topology_edges", edge_ids == expected_edges,
           f"saved={sorted(edge_ids)}")
    route = extra.get("agv_route") or {}
    expected_docks = {
        unit_id: [round(float(v), 6) for v in pos]
        for unit_id, pos in default_layout().dock_positions.items()}
    saved_docks = {str(key): [round(float(v), 6) for v in value]
                   for key, value in (route.get("docks") or {}).items()}
    _check(checks, "stage4_route_docks", saved_docks == expected_docks,
           f"saved={saved_docks}")
    material_model = extra.get("material_model") or {}
    _check(checks, "stage4_material_types",
           list(material_model.get("types") or ()) == list(cfg.ITEM_TYPES),
           f"saved={material_model.get('types')}")
    join = material_model.get("join") or {}
    _check(checks, "stage4_join_inputs",
           list(join.get("inputs") or ()) == list(cfg.JOIN_INPUTS),
           f"saved={join.get('inputs')}")
    _check(checks, "stage4_join_output",
           join.get("output") == cfg.JOIN_OUTPUT,
           f"saved={join.get('output')}")
    flow = extra.get("task_flow") or {}
    _check(checks, "stage4_task_kinds",
           set(flow.get("kinds") or ()) == set(cfg.TASK_KINDS),
           f"saved={flow.get('kinds')}")

    index = _resolve_aliases(sim)
    for alias in cfg.REQUIRED_ALIASES:
        hits = index.get(alias, [])
        _check(checks, f"stage4_alias_unique:{alias}", len(hits) == 1,
               f"{len(hits)} 个匹配")
    shadow = sorted({alias for alias, hits in index.items()
                     if alias.startswith(cfg.SESSION_RESOURCE_PREFIX)
                     for _ in hits})
    _check(checks, "stage4_no_session_resources", not shadow,
           f"found={shadow}")

    if scene is not None:
        try:
            units = getattr(scene, "units", {})
            _check(checks, "stage4_scene_units",
                   set(units) == set(cfg.UNIT_IDS), f"units={sorted(units)}")
            for unit_id in cfg.UNIT_IDS:
                unit = units.get(unit_id)
                if unit is None:
                    _check(checks, f"stage4_unit:{unit_id}", False, "missing")
                    continue
                _check(checks, f"stage4_unit:{unit_id}",
                       len(unit.robot_joints) == 6,
                       f"joints={len(unit.robot_joints)}")
                modes = []
                for joint in unit.robot_joints:
                    mode = sim.getJointMode(joint)
                    modes.append(int(mode[0] if isinstance(mode, (tuple, list))
                                     else mode))
                _check(checks, f"stage4_joint_modes:{unit_id}",
                       all(mode == int(sim.jointmode_kinematic)
                           for mode in modes), f"modes={modes}")
                local = list(sim.getObjectPosition(unit.tcp,
                                                   unit.suction_root))
                _check(checks, f"stage4_tcp_offset:{unit_id}",
                       all(abs(local[i] - base.TCP_LOCAL_POS[i])
                           <= base.SUCTION_MOUNT_POS_TOL for i in range(3)),
                       f"TCP local={[round(v, 5) for v in local]}")
                if unit.spec.has_dock:
                    pose = list(sim.getObjectPosition(
                        unit.dock_sensor, sim.handle_world))
                    expected = [unit.spec.dock[0], unit.spec.dock[1],
                                base.AGV_DOCK_SENSOR_Z]
                    _check(checks, f"stage4_dock_sensor:{unit_id}",
                           all(abs(pose[i] - expected[i]) <= 0.002
                               for i in range(3)),
                           f"pos={[round(v, 4) for v in pose]}")
            report = scene.verify()
            _check(checks, "stage4_scene_contract", bool(report.get("ok")),
                   f"{sum(1 for c in report.get('checks', []) if c['ok'])}"
                   f"/{len(report.get('checks', []))} checks")
            for item in report.get("checks", []):
                if not item.get("ok"):
                    differences.append({"kind": "contract",
                                        "name": item.get("name"),
                                        "detail": item.get("detail")})
        except Exception as exc:
            _check(checks, "stage4_scene_observable", False,
                   f"{type(exc).__name__}: {exc}")

    ok = sa.checks_ok(checks) and not differences
    return {
        "ok": ok,
        "contract_version": extra.get("contract_version"),
        "checks": checks,
        "differences": differences,
    }
